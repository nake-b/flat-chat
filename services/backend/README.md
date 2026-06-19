# flat-chat backend

FastAPI backend for the Berlin Apartment AI chatbot. Pydantic AI agent over a SearchService backed by PostgreSQL + pgvector + PostGIS.

See [`CLAUDE.md`](../../CLAUDE.md) for project-wide architecture notes and Pydantic AI patterns.

## Setup

```bash
brew install just       # task runner (one-time)
uv sync                 # install all dependencies
```

Env vars are read from the project-root `.env` (justfile uses `set dotenv-load`). The only required var is `DATABASE_URL` — the hackathon starter boots with no LLM keys (it ships a no-LLM placeholder agent). Add your own agent's keys when you wire in a framework. See the table below.

## Running

```bash
just dev                # start uvicorn with reload (uses .env)
# or from project root, in the full compose network:
docker compose up backend
```

## Quality Checks

```bash
just              # list all commands
just check        # lint + typecheck + test
just lint         # ruff check
just typecheck    # ty check
just test         # pytest (passes args: just test -k health)
just format       # ruff format
just fix          # auto-fix lint + format
```

CI runs the same checks on every push and PR — see [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml).

## API Endpoints

| Endpoint                                  | Method | Description                                                                                                |
|-------------------------------------------|--------|------------------------------------------------------------------------------------------------------------|
| `/api/health`                             | GET    | Health check                                                                                               |
| `/api/conversations`                      | POST   | Create a conversation; returned id doubles as the AG-UI `thread_id`                                        |
| `/api/conversations/{id}/messages`        | GET    | Get message history (history reload after page refresh — read-only)                                        |
| `/api/agent`                              | POST   | AG-UI Protocol streaming endpoint. SSE: text deltas, tool-call lifecycle, JSON-Patch `UiState` deltas      |

The frontend uses relative URLs (`/api/...`) so the same calls work via the Vite dev proxy and the production Nginx. Sending a new user message goes through `/api/agent` (AG-UI streaming). The legacy `POST /api/conversations/{id}/messages` REST endpoint was removed when the agent path landed.

## Project Layout

```
src/flat_chat/
├── main.py              # FastAPI app, lifespan, router registration
├── core/
│   ├── config.py        # Pydantic Settings (env vars; required fields use Field(...))
│   ├── database.py      # SQLAlchemy engine, session, Base
│   ├── embedder.py      # Jina embedder factory (singleton via app.state)
│   ├── dependencies.py  # FastAPI Depends wiring (session store, services)
│   └── observability.py # Logs (dictConfig) + traces (OpenTelemetry → Phoenix)
├── api/
│   ├── chat.py          # Conversation lifecycle: POST create + GET history reload (no message-send)
│   └── agent.py         # POST /api/agent — AG-UI SSE; ChatService runs the AgentBackend
├── chat/
│   ├── backend.py       # ★ AgentBackend Protocol (the seam) + ag_ui event helpers
│   ├── example_backend.py # ★ ExampleSearchBackend — no-LLM placeholder; REPLACE THIS
│   ├── state.py         # ChatSession (history + state), ChatMessage, ChatDeps
│   ├── session_state.py # Frontend mirror: SessionState + (re-exported) UiApartment
│   ├── sessions.py      # SessionStore Protocol + InMemorySessionStore (per-session asyncio.Lock)
│   ├── service.py       # ChatService — parses AG-UI, brackets the run, SSE-encodes, persists
│   └── schemas.py       # API response models
└── search/
    ├── models.py              # Listing SQLAlchemy model (HNSW + functional GIST indexes)
    ├── geo_models.py          # SQLAlchemy mirrors of the 14 geo-context silver tables
    ├── schemas.py             # SearchParams (Literal sort_by, Field-bounded limit/radius_km)
    ├── geo_filters.py         # Pydantic filter schemas (TransitFilter/SchoolFilter/HospitalFilter/MssFilter) + ListingContext shape
    ├── distances.py           # Distance bucket constants + walk-minute helper + per-dataset caps
    ├── buckets.py             # Noise / density / greenery bucket classifiers (absolute WHO/EU thresholds)
    ├── transit.py             # GTFS Extended mode codes ↔ English enum mapping
    ├── service.py             # SearchService — structured + vector + geo; composes GeoContextService
    └── geo_context_service.py # Internal seam owning all geo-context table access (predicates, chip LATERALs, context_for)
tests/                         # Test suite (pytest)
```

Key idioms:
- **`ResultSet` owns all LLM-facing listing formatting** — `summary` / `page` / `detail` / `describe_for_instructions`. Any new listing surface goes here, not in tools. See [`agent-compound-docs/decisions/llm-tool-result-design.md`](../../agent-compound-docs/decisions/llm-tool-result-design.md).
- **`UiState` is the frontend-facing mirror** — a parallel projection of the same search results, *not* a replacement for `ResultSet`. Tools mutate both per call; the agent only ever reads `ResultSet`, the React app only ever reads `UiState` via AG-UI shared state. See [`agent-compound-docs/decisions/frontend-stack.md`](../../agent-compound-docs/decisions/frontend-stack.md).
- **`ChatDeps` satisfies the AG-UI `StateHandler` protocol** by exposing a `state: UiState` dataclass field. The `AGUIAdapter` sets this from each incoming request and streams JSON Patch deltas of subsequent tool mutations back to the frontend.
- **Domain services take `db: Session` in the constructor** — framework-agnostic; works in FastAPI, scripts, and tests.
- **All cross-layer wiring goes through FastAPI `Depends`** in `core/dependencies.py`. No module-level singletons in the request path beyond the session store.
- **`GeoContextService` is the internal seam for the 14 geo-context silver tables.** `SearchService` is the agent-facing facade; it composes `GeoContextService` for (a) pre-filter SQL predicates (`filter_predicates(params)`), (b) always-on per-card chip `LATERAL` joins (`chip_joins()`), and (c) the fat per-listing context blob returned by `get_listing_details` (`context_for(location)`). The agent only ever sees `SearchService`. See [Geo-context interpretation defaults](#geo-context-interpretation-defaults).

## Geo-context interpretation defaults

The agent translates natural phrases like "near a school", "quiet street", "affluent neighbourhood" into structured filters using a fixed set of numeric thresholds and labels. Every constant traces to an external authority (WHO, EU END, urban planning literature, Berlin Senate docs). The full audit trail with sources and Berlin-delta rationale lives at [`agent-compound-docs/decisions/geo-context-thresholds.md`](../../agent-compound-docs/decisions/geo-context-thresholds.md) — read it before changing any threshold.

Quick reference:

| Concept | Default(s) | Authority |
|---|---|---|
| Walking distance buckets | `next_to=150m`, `very_near=400m`, **`near=650m`** (default), `walking_distance=1200m`, `bike_distance=2500m` | CNU pedestrian shed, German "fußläufig" (DWDS), Calthorpe TOD |
| Pedestrian speed | `1.4 m/s` (used for walk-minute conversion) | WHO/EAÖ standard adult walking speed |
| Noise (Lden, dB) | `quiet < 55`, `lively 55–65`, `noisy ≥ 65` | WHO 2018 + EU END thresholds |
| Greenery | `leafy = ≥0.5 ha green ≤300m`; `very_leafy = doubled` | WHO Europe / 3-30-300 rule |
| Cemeteries (Friedhöfe) | Counted in green amenity at **0.5 weight**; NEVER shown as the `nearest_park` chip | Senate policy + cultural usage; gloomy-perception caveat |
| Density (persons/ha) | `sparse < 50`, `moderate 50–150`, `dense ≥ 150` | General urban planning |
| Transit modes (tool-facing) | `u_bahn / s_bahn / tram / bus / ferry / regional / mainline` (English enum) | GTFS Extended Route Types (DB stores ints, tool surface uses strings) |
| MSS status labels | German → English: `hoch → affluent`, `mittel → mixed`, `niedrig → lower-income`, `sehr niedrig → disadvantaged` | Berlin Senate Sozialmonitoring 2023 methodology |
| MSS dynamics labels | `positiv → improving`, `stabil → stable`, `negativ → slipping` (counter-intuitive — measures relative-to-citywide trend) | Same |

**Agent neutrality requirement**: MSS labels are *not* value judgements. `affluent` is not a recommendation; `disadvantaged` is not a warning. The agent's `INSTRUCTIONS` enforces neutral framing — never volunteer opinions about neighbourhood status.

**Rule**: when adding a new constant, add a row to the threshold doc *first*, then write the code that references it. Constants without an entry there are technical debt.

## Configuration

Values are read from environment variables (set via root `.env` or Docker Compose).

| Variable                   | Description                                                                                                            | Default                            |
|----------------------------|------------------------------------------------------------------------------------------------------------------------|------------------------------------|
| `DATABASE_URL`             | PostgreSQL connection string                                                                                           | — (required)                       |
| _(your agent's keys)_      | The starter ships with a no-LLM placeholder agent and needs no LLM keys. Add your framework's config in `core/config.py` + `.env.example` + compose. | —                |
| `JINA_API_KEY`             | Jina embeddings API key (optional — empty disables semantic search)                                                    | —                                  |
| `JINA_BASE_URL`            | Jina API base URL                                                                                                      | `https://api.jina.ai/v1`           |
| `PHOENIX_ENABLED`          | Enable Phoenix observability                                                                                           | `false`                            |
| `PHOENIX_ENDPOINT`         | Phoenix OTLP endpoint                                                                                                  | `http://localhost:6006/v1/traces`  |
| `LOG_LEVEL`                | Log level for the `flat_chat` namespace (DEBUG / INFO / WARNING / ERROR). Third-party loggers stay at WARNING.         | `INFO`                             |

## Debugging

Every request gets a session id (the conversation) and a run id (this turn). Both show up in two places:

1. Every backend log line gets a `[session=<uuid> run=<run_id>]` prefix (see `core/observability.py:_RequestContextFilter`).
2. Every SQL statement fired from inside that request gets a `/* session=<uuid> run=<run_id> */` comment prepended (see the `before_cursor_execute` hook in `core/database.py`). Startup queries, Alembic migrations, and pool pre-pings carry no comment — the contextvars are only set during request handling.

This lets you round-trip between application logs and Postgres state.

### Symptom → playbook

**"A turn is taking forever"** — find the stuck query:

```bash
just psql-active
```

Lists running queries oldest-first. The `query` column starts with `/* session=<uuid> run=<run_id> */`, so the oldest row tells you which conversation/turn is wedged.

**"I have a session id from the logs, what is it doing right now?"**

```bash
just psql-session <session-uuid>
# or
just psql-session <run-id>
```

**"I have a stuck query, what is the conversation context?"**

Copy the `session=<uuid>` value from `pg_stat_activity`, then:

```bash
docker compose logs backend | grep <session-uuid>
```

You get the full log trail — `Agent dispatch` → `Searching: {…}` → (stalled) — for that one conversation.

**"What is the LLM doing right now?"**

Phoenix at [http://localhost:6006](http://localhost:6006) shows in-flight LLM spans, tool calls, and tokens. Use Phoenix for the agent's "thinking" side; use the logs above for everything below the agent (search service, SQL, ORM).

### Free-form psql

```bash
just psql      # interactive shell on the dev postgres
```
