# flat-chat backend

FastAPI backend for the Berlin Apartment AI chatbot. Pydantic AI agent over a SearchService backed by PostgreSQL + pgvector + PostGIS.

See [`CLAUDE.md`](../../CLAUDE.md) for project-wide architecture notes and Pydantic AI patterns.

## Setup

```bash
brew install just       # task runner (one-time)
uv sync                 # install all dependencies
```

Env vars are read from the project-root `.env` (justfile uses `set dotenv-load`). Required: `DATABASE_URL` plus an LLM provider key — `OPENAI_API_KEY` (preferred), `ANTHROPIC_API_KEY` (prompt caching), or the full Azure OpenAI quartet (`AZURE_OPENAI_API_KEY` + `_ENDPOINT` + `_DEPLOYMENT` + `_API_VERSION`). Preference order when several are set: OpenAI > Anthropic > Azure. See the table below.

## Running

```bash
just dev                # start uvicorn with reload (uses .env)
# or from project root, in the full compose network:
docker compose up backend
```

## Quality Checks

```bash
just              # list all commands
just check        # lint + format-check + typecheck + test (mirrors CI exactly)
just lint         # ruff check src tests
just format-check # ruff format --check src tests (fails on unformatted)
just typecheck    # ty check src
just test         # pytest (passes args: just test -k health)
just format       # ruff format src tests (rewrites in place)
just fix          # auto-fix lint + format
```

CI runs the same checks on every push and PR — see [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml).
`just check` is kept in lock-step with CI's step order (ruff check →
ruff format --check → ty → pytest) and scope (`src` *and* `tests`), so a
green `just check` means green CI.

## API Endpoints

**Interactive docs / machine-readable spec.** FastAPI auto-generates an OpenAPI
3.1 schema. In local dev (backend on `:8000`) it's served at
[`/docs`](http://localhost:8000/docs) (Swagger UI),
[`/redoc`](http://localhost:8000/redoc), and `/openapi.json`. These are *not*
exposed through Nginx — this is an internal BFF, so the docs stay a dev/demo
convenience. A static copy of the spec is committed at
[`docs/openapi.json`](docs/openapi.json) (the artifact to read without running
the stack); regenerate it after changing any route with:

```bash
uv run python scripts/export_openapi.py   # no running server needed
```

| Endpoint                                  | Method | Description                                                                                                |
|-------------------------------------------|--------|------------------------------------------------------------------------------------------------------------|
| `/api/health`                             | GET    | Health check                                                                                               |
| `/api/auth/login` · `/logout`             | POST   | fastapi-users cookie auth (login is an OAuth2 password form). Sets/clears the httpOnly session cookie       |
| `/api/auth/me`                            | GET    | Current authenticated user. (No public `/register` — accounts are seed-only)                  |
| `/api/conversations`                      | POST   | Create a conversation (persisted in `app.*`); returned id doubles as the AG-UI `thread_id`. Auth required  |
| `/api/conversations`                      | GET    | List the calling user's conversations with at least one persisted message — powers the sidebar             |
| `/api/conversations/{id}`                 | DELETE | Hard-delete a conversation owned by the caller; cascades to messages + session_state; 204 on success       |
| `/api/conversations/{id}/messages`        | GET    | Get message history (history reload after page refresh — read-only, ownership-checked)                     |
| `/api/conversations/{id}/state`           | GET    | Latest `SessionState` snapshot — the reload-recovery primitive (map/cards/active listing), ownership-checked |
| `/api/agent`                              | POST   | AG-UI Protocol streaming endpoint. SSE: text deltas, tool-call lifecycle, JSON-Patch `SessionState` deltas. Auth + ownership-checked (404-not-403) |
| `/api/listings/{id}`                      | GET    | Tier-3 listing detail + image gallery (`Cache-Control: public, max-age=300`). Backs the card detail panel and the agent's `open_listing` tool. `422` on malformed id |
| `/api/listings?ids=&view=card`            | GET    | Batch tier-2 card hydration in request order (≤100 ids, cacheable) — lazy-loads cards past the preview window |
| `/api/bookmarks/{listing_id}`             | POST · DELETE | Add / remove a bookmark for the calling user (idempotent). `422` on malformed id                       |
| `/api/bookmarks` · `/api/bookmarks/ids`   | GET    | List the user's bookmarks — tier-2 cards (`/`) or just ids (`/ids`, fast star hydration)                       |

The frontend uses relative URLs (`/api/...`) so the same calls work via the Vite dev proxy and the production Nginx. Sending a new user message goes through `/api/agent` (AG-UI streaming). The legacy `POST /api/conversations/{id}/messages` REST endpoint was removed when the agent path landed.

## Project Layout

See [`CLAUDE.md`](CLAUDE.md) for the annotated per-module breakdown; the high-level tree is:

```
src/flat_chat/
├── main.py              # FastAPI app, lifespan, router registration
├── core/                # config, DB engines (sync + async), embedder, deps, observability
├── api/                 # Thin HTTP routes
│   ├── auth.py          # fastapi-users routers under /api/auth
│   ├── chat.py          # POST/GET /api/conversations + DELETE /{id} + GET messages/state
│   ├── agent.py         # POST /api/agent — AG-UI streaming via AGUIAdapter.dispatch_request
│   ├── listings.py      # GET /api/listings/{id} (detail) + GET /api/listings?ids=&view=card (batch tier-2)
│   └── bookmarks.py     # POST/DELETE /api/bookmarks/{listing_id} + GET /api/bookmarks(/ids)
├── users/               # Identity domain (app schema)
│   ├── models.py        # User ORM (fastapi-users columns)
│   └── auth.py          # fastapi-users wiring (UserManager, cookie+JWT backend, current_active_user)
├── chat/                # Agent orchestration domain
│   ├── agent.py         # Pydantic AI Agent(capabilities=[...]) + static instructions
│   ├── tools/           # FunctionToolset capabilities: core (search/open/page/locate), overlays,
│   │                    #   lenses, proximity (deferred) + emission (auto STATE_SNAPSHOT) + backbone
│   ├── providers/       # Provider dispatch — single seam (OpenAI / Anthropic / Azure)
│   │   ├── __init__.py  # build_chat_model()/build_title_model() — @lru_cache; picks provider by key presence
│   │   ├── openai.py    # standard (non-Azure) OpenAI model
│   │   ├── anthropic.py # AnthropicModel + prompt-caching breakpoints
│   │   └── azure.py     # Azure OpenAI Service model
│   ├── llm_context.py   # LlmResultSetView (LLM-facing prose) + build_dynamic_state_prompt
│   ├── session_state.py # SessionState — canonical per-conversation snapshot (markers/cards/facets)
│   ├── state.py         # ChatSession (history + SessionState + user_id) + ChatDeps (StateHandler-compatible)
│   ├── sessions.py      # SessionStore Protocol + InMemory + DbSessionStore (Postgres, per-session lock)
│   ├── service.py       # ChatService — dispatches AG-UI runs, history-authoritative, persists state/history
│   ├── title_gen.py     # TitleGenerationService (background sidebar-title task after first turn)
│   ├── models.py        # app-schema ORMs: Conversation, Message, SessionStateRow
│   └── schemas.py       # API response models
├── search/              # Query-execution domain (agent-only)
│   ├── service.py       # SearchService — async; returns (markers, preview_cards, total, facets)
│   ├── schemas.py       # SearchParams + SortBy (near_place_ref, inside_ring, kita, …)
│   ├── geo_filters.py   # Filter input shapes (TransitFilter/SchoolFilter/HospitalFilter/KitaFilter)
│   ├── places.py        # PlaceService — locate_place trigram lookup over world.named_places + overlay geometry
│   ├── distance.py      # DistanceService — {id: metres} via ST_Distance (the distance-lens provider)
│   └── transit_overlays.py # TransitOverlayService — line → route-shape GeoJSON + served stations (display only)
├── listings/            # Shared listing-domain primitives (leaf module)
│   ├── models.py        # Listing + ListingGeoContext + ListingNearby* + named_places + transit ORMs (read-only world.*)
│   ├── service.py       # ListingService — async get_detail(id) / get_cards(ids)
│   ├── projection.py    # Shared tier-2 ListingCard projection (preview + get_cards)
│   ├── context.py       # ListingDetail + ListingCard + Marker + Anchor
│   ├── lenses.py        # MarkerLens + ActiveLens union (TravelTimeLens | DistanceLens)
│   ├── overlays.py      # MapOverlay + OverlayPoint + OVERLAY_* consts
│   ├── labels.py        # bucket_*, walk_minutes, encode_modes, …
│   ├── thresholds.py    # Single source of truth for numeric constants
│   ├── types.py         # Literal label types (NoiseLabel, DensityLabel, GreeneryLabel, …)
│   ├── geo.py           # equirect_distance_m — cheap in-memory point math
│   └── bookmarks/       # Bookmark subpackage (app schema): Bookmark ORM + BookmarkService
tests/                   # Test suite (pytest) — unit + integration tiers
```

Key idioms:
- **`LlmResultSetView` (in `chat/llm_context.py`) owns all LLM-facing listing prose** — `summary` / `page` / `detail`. Any new LLM-facing listing surface goes there, not in tools. See [`agent-compound-docs/decisions/llm-tool-result-design.md`](../../agent-compound-docs/decisions/llm-tool-result-design.md).
- **`SessionState` (in `chat/session_state.py`) is the single per-conversation snapshot** — markers + preview cards + facets + active listing. It is read by three consumers: the LLM (via `build_dynamic_state_prompt`), the frontend (via AG-UI shared state), and the pagination tool. Tools mutate `deps.state`; `StateEmittingToolset` auto-emits a `STATE_SNAPSHOT` on any change. See [`agent-compound-docs/decisions/session-state-design.md`](../../agent-compound-docs/decisions/session-state-design.md).
- **`ChatDeps` satisfies the AG-UI `StateHandler` protocol** by exposing a `state` field. The `AGUIAdapter` sets it from each incoming request and streams JSON Patch deltas of subsequent tool mutations back to the frontend.
- **Domain services take `db` in the constructor** — framework-agnostic; work in FastAPI, scripts, and tests.
- **All cross-layer wiring goes through FastAPI `Depends`** in `core/dependencies.py`. No module-level singletons in the request path beyond the session store.
- **Search runs against the gold table.** `SearchService` joins `listings ⨝ listings_geo_context (⨝ listings_embeddings)` — all geo-context filters are B-tree predicates on gold's denormalised columns; POI filters are `EXISTS` against the `listings_nearby_*` junction tables. There is no separate `GeoContextService` seam. `SearchService` is agent-only; `ListingService` (shared) powers direct id reads. See [Geo-context interpretation defaults](#geo-context-interpretation-defaults).

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
| Noise (Lnight, dB) | Detail-only night metric alongside Lden; filtering stays on Lden | WHO 2018 night-noise guideline |
| "Inside the ring" | Umweltzone (low-emission zone) polygon ≈ S-Bahn ring "Hundekopf" → `inside_ring` | Berlin LEZ legal boundary; agent reads "city center"/"Zentrum" as the ring (polycentric city) |

> **MSS / Sozialmonitoring was removed entirely in geo-context v2** (ethical grounds). The status/dynamics labels, the `mss_*` columns, `MssFilter`, and the agent neutrality block are gone. See [`named-place-search.md`](../../agent-compound-docs/decisions/named-place-search.md) and [`bezirk-ortsteil-resolution.md`](../../agent-compound-docs/decisions/bezirk-ortsteil-resolution.md).

**Rule**: when adding a new constant, add a row to the threshold doc *first*, then write the code that references it. Constants without an entry there are technical debt.

## Configuration

Values are read from environment variables (set via root `.env` or Docker Compose).

| Variable                   | Description                                                                                                            | Default                            |
|----------------------------|------------------------------------------------------------------------------------------------------------------------|------------------------------------|
| `DATABASE_URL`             | PostgreSQL connection string                                                                                           | — (required)                       |
| `OPENAI_API_KEY`           | OpenAI API key (standard, non-Azure). **Preferred provider** when set (order: OpenAI > Anthropic > Azure).             | — (one provider required)          |
| `OPENAI_MODEL`             | OpenAI chat model id (e.g. `gpt-5.4`, `gpt-5.5`)                                                                       | `gpt-5.4`                          |
| `OPENAI_TITLE_MODEL`       | Cheap/fast OpenAI model for one-shot sidebar title generation                                                          | `gpt-5.4-nano`                     |
| `OPENAI_BASE_URL`          | Optional base-URL override for OpenAI-compatible endpoints/proxies. Empty = OpenAI default.                            | `""`                               |
| `ANTHROPIC_API_KEY`        | Anthropic API key (native prompt caching). One LLM provider (OpenAI, Anthropic, or the Azure quartet) must be set.     | — (one provider required)          |
| `ANTHROPIC_MODEL`          | Anthropic model id (e.g. `claude-sonnet-4-6`, `claude-haiku-4-5`)                                                      | `claude-sonnet-4-6`                |
| `ANTHROPIC_TITLE_MODEL`    | Cheap/fast model for one-shot sidebar title generation. Defaults to Haiku.                                             | `claude-haiku-4-5-20251001`        |
| `AZURE_OPENAI_API_KEY`     | Azure OpenAI Service key. Used when OpenAI and Anthropic are both unset.                                               | —                                  |
| `AZURE_OPENAI_ENDPOINT`    | e.g. `https://<resource>.openai.azure.com/`                                                                            | —                                  |
| `AZURE_OPENAI_DEPLOYMENT`  | Deployment name from Foundry (often matches the model name)                                                            | —                                  |
| `AZURE_OPENAI_API_VERSION` | API version — use a preview version for o-series reasoning models                                                      | `2024-12-01-preview`               |
| `AZURE_OPENAI_TITLE_DEPLOYMENT` | Optional separate Azure deployment for title generation. Empty = reuse `AZURE_OPENAI_DEPLOYMENT`.                 | `""`                               |
| `LLM_DAILY_TOKEN_BUDGET`   | Per-user rolling-24h `total_tokens` cap (pre-run 429 gate, keyed on `get_user_id()`). `0` disables the per-user gate (a per-run runaway backstop still applies). See `llm-rate-limit.md`. | `2000000`                          |
| `JINA_API_KEY`             | Jina embeddings API key (optional — empty disables semantic search)                                                    | —                                  |
| `JINA_BASE_URL`            | Jina API base URL                                                                                                      | `https://api.jina.ai/v1`           |
| `PHOENIX_ENABLED`          | Enable Phoenix observability                                                                                           | `false`                            |
| `PHOENIX_ENDPOINT`         | Phoenix OTLP endpoint                                                                                                  | `http://localhost:6006/v1/traces`  |
| `LOG_LEVEL`                | Log level for the `flat_chat` namespace (DEBUG / INFO / WARNING / ERROR). Third-party loggers stay at WARNING.         | `INFO`                             |
| `OSRM_URL`                 | OSRM car-routing engine (the `routing` profile). Used by `apply_travel_time` (mode=car). Degrades gracefully if down.  | `http://osrm:5000`                 |
| `MOTIS_URL`                | MOTIS transit-routing engine (the `routing` profile). Used by `apply_travel_time` (mode=transit). Degrades gracefully. | `http://motis:8080`                |
| `JWT_SECRET`               | Signs the fastapi-users login cookie. **Required** (no insecure default ships). `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Rotating it logs everyone out. | — (required)                       |
| `JWT_LIFETIME_SECONDS`     | Login cookie lifetime                                                                                                  | `604800` (7 days)                  |
| `COOKIE_SECURE`            | Login cookie `Secure` attribute — `false` for local HTTP, `true` for HTTPS deploys                                     | `false`                            |
| `DEV_USER_EMAIL`           | Seeded admin login email (`scripts/seed_users.py`)                                                                     | `dev@flatchat.dev`                 |
| `DEV_USER_PASSWORD`        | Seeded admin login password — override in any non-local deployment                                                     | `dev`                              |
| `PROF_USER_EMAIL`          | Optional reviewer login (regular user) — seeded only when both prof vars are set                                       | — (empty)                          |
| `PROF_USER_PASSWORD`       | Optional reviewer login password                                                                                       | — (empty)                          |

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
