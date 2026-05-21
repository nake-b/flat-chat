# flat-chat backend

FastAPI backend for the Berlin Apartment AI chatbot. Pydantic AI agent over a SearchService backed by PostgreSQL + pgvector + PostGIS.

See [`CLAUDE.md`](../../CLAUDE.md) for project-wide architecture notes and Pydantic AI patterns.

## Setup

```bash
brew install just       # task runner (one-time)
uv sync                 # install all dependencies
```

Env vars are read from the project-root `.env` (justfile uses `set dotenv-load`). Required: `DATABASE_URL`, `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`. See the table below.

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

| Endpoint                                  | Method | Description           |
|-------------------------------------------|--------|-----------------------|
| `/api/health`                             | GET    | Health check          |
| `/api/conversations`                      | POST   | Create a conversation |
| `/api/conversations/{id}/messages`        | POST   | Send a message        |
| `/api/conversations/{id}/messages`        | GET    | Get message history   |

Chat uses an app-level REST shape (not OpenAI-style). The frontend uses relative URLs (`/api/...`) so the same calls work via the Vite dev proxy and the production Nginx.

## Project Layout

```
src/flat_chat/
├── main.py              # FastAPI app, lifespan, router registration
├── core/
│   ├── config.py        # Pydantic Settings (env vars; required fields use Field(...))
│   ├── database.py      # SQLAlchemy engine, session, Base
│   ├── embedder.py      # Jina embedder factory (singleton via app.state)
│   ├── dependencies.py  # FastAPI Depends wiring (session store, services)
│   └── observability.py # Phoenix / OpenTelemetry — Agent.instrument_all()
├── api/
│   └── chat.py          # Thin FastAPI router; serializes ModelMessage history
├── chat/
│   ├── agent.py         # Pydantic AI Agent + INSTRUCTIONS + run_agent()
│   ├── tools.py         # FunctionToolset[ChatDeps]: search / page / details
│   ├── state.py         # ChatSession, ResultSet (central LLM-facing formatter), ChatDeps
│   ├── sessions.py      # SessionStore Protocol + InMemorySessionStore (per-session asyncio.Lock)
│   ├── service.py       # ChatService orchestration
│   ├── schemas.py       # API request/response models
│   └── providers/       # Chat-model dispatch — single provider seam
│       ├── __init__.py  # build_chat_model() — @lru_cache; picks providers from settings
│       └── openrouter.py # OpenRouterModel subclass: retries body-embedded 5xx/429
└── search/
    ├── models.py        # Listing SQLAlchemy model (HNSW + functional GIST indexes)
    ├── schemas.py       # SearchParams (Literal sort_by, Field-bounded limit/radius_km)
    └── service.py       # SearchService — structured + vector + geo (Geography cast)
tests/                   # Test suite (pytest)
```

Key idioms:
- **`ResultSet` owns all LLM-facing listing formatting** — `summary` / `page` / `detail` / `describe_for_instructions`. Any new listing surface goes here, not in tools. See [`agent-compound-docs/decisions/llm-tool-result-design.md`](../../agent-compound-docs/decisions/llm-tool-result-design.md).
- **Domain services take `db: Session` in the constructor** — framework-agnostic; works in FastAPI, scripts, and tests.
- **All cross-layer wiring goes through FastAPI `Depends`** in `core/dependencies.py`. No module-level singletons in the request path beyond the session store.

## Configuration

Values are read from environment variables (set via root `.env` or Docker Compose).

| Variable             | Description                                                                                                                                       | Default                            |
|----------------------|---------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------|
| `DATABASE_URL`       | PostgreSQL connection string                                                                                                                      | — (required)                       |
| `OPENROUTER_API_KEY` | OpenRouter API key                                                                                                                                | —                                  |
| `OPENROUTER_MODEL`   | Model slug (`org/model:tag`) or preset (`@preset/<slug>`). Presets configured at [openrouter.ai/settings/presets](https://openrouter.ai/settings/presets) | — (required)                       |
| `JINA_API_KEY`       | Jina embeddings API key (optional — empty disables semantic search)                                                                                | —                                  |
| `JINA_BASE_URL`      | Jina API base URL                                                                                                                                 | `https://api.jina.ai/v1`           |
| `PHOENIX_ENABLED`    | Enable Phoenix observability                                                                                                                      | `false`                            |
| `PHOENIX_ENDPOINT`   | Phoenix OTLP endpoint                                                                                                                             | `http://localhost:6006/v1/traces`  |
