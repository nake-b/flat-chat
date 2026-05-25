# flat-chat backend

FastAPI backend for the Berlin Apartment AI chatbot. Pydantic AI agent over a SearchService backed by PostgreSQL + pgvector + PostGIS.

See [`CLAUDE.md`](../../CLAUDE.md) for project-wide architecture notes and Pydantic AI patterns.

## Setup

```bash
brew install just       # task runner (one-time)
uv sync                 # install all dependencies
```

Env vars are read from the project-root `.env` (justfile uses `set dotenv-load`). Required: `DATABASE_URL` and `ANTHROPIC_API_KEY`. See the table below.

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

### Chat runtime (how the streaming POST actually works)

Per turn, the frontend POSTs an AG-UI envelope (`thread_id` + full message history + current `UiState` mirror) to `/api/agent`. The backend resolves the session from `thread_id`, acquires a per-session async lock, runs the Pydantic AI agent via `AGUIAdapter.run_stream`, and streams SSE events back on the same response body — text deltas, tool-call lifecycle, and `STATE_SNAPSHOT` events (the latter opt-in via `ToolReturn(metadata=[...])` in tools that mutate state). After the run, `on_complete(result)` rebuilds `session.message_history` from `result.all_messages()` and saves.

Load-bearing files:
- [`api/agent.py`](src/flat_chat/api/agent.py) — `POST /api/agent` route; runtime-discovery GET probes (`/info`, `/threads`).
- [`chat/service.py`](src/flat_chat/chat/service.py) — `ChatService.dispatch_agent_request` (envelope parsing, exception translation, per-session locking), `_with_session_and_lock` generator wrapper, `on_complete` persistence hook.
- [`chat/sessions.py`](src/flat_chat/chat/sessions.py) — `InMemorySessionStore` with `_MAX_SESSIONS = 100` LRU cap and per-session `asyncio.Lock`. `lock()` raises on unknown ids (DoS guard).
- [`chat/tools.py`](src/flat_chat/chat/tools.py) — `_return_with_state` helper. Every tool that mutates `UiState` must use it; mutations otherwise stay invisible to the frontend.
- [`../../nginx/nginx.conf`](../../nginx/nginx.conf) — `location /api/agent` block with `proxy_buffering off` and `Accept-Encoding ""` strip (essentials for SSE through a proxy).

Full pipeline, rationale for each layer, exception translation table, rejected alternatives, and the answer to "why are state events opt-in?" live in [`agent-compound-docs/decisions/chat-runtime-and-streaming.md`](../../agent-compound-docs/decisions/chat-runtime-and-streaming.md). Read that before touching any of the files above.

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
│   ├── chat.py          # Conversation lifecycle: POST create + GET history reload (no message-send)
│   └── agent.py         # POST /api/agent — AG-UI streaming via AGUIAdapter.dispatch_request
├── chat/
│   ├── agent.py         # Pydantic AI Agent + INSTRUCTIONS + dynamic-instruction injection
│   ├── tools.py         # FunctionToolset[ChatDeps]: search / page / details; mirrors into UiState
│   ├── state.py         # ChatSession (history + ResultSet + ui_state), ChatDeps (StateHandler-compatible)
│   ├── ui_state.py      # Frontend mirror: UiState + UiApartment Pydantic models
│   ├── sessions.py      # SessionStore Protocol + InMemorySessionStore (per-session asyncio.Lock)
│   ├── service.py       # ChatService — dispatches AG-UI runs and persists state/history
│   ├── schemas.py       # API response models
│   └── providers/       # Chat-model dispatch — single provider seam
│       ├── __init__.py  # build_chat_model() — @lru_cache; picks provider from settings
│       └── anthropic.py # AnthropicModel + prompt caching settings
└── search/
    ├── models.py        # Listing SQLAlchemy model (HNSW + functional GIST indexes)
    ├── schemas.py       # SearchParams (Literal sort_by, Field-bounded limit/radius_km)
    └── service.py       # SearchService — structured + vector + geo (Geography cast)
tests/                   # Test suite (pytest)
```

Key idioms:
- **`ResultSet` owns all LLM-facing listing formatting** — `summary` / `page` / `detail` / `describe_for_instructions`. Any new listing surface goes here, not in tools. See [`agent-compound-docs/decisions/llm-tool-result-design.md`](../../agent-compound-docs/decisions/llm-tool-result-design.md).
- **`UiState` is the frontend-facing mirror** — a parallel projection of the same search results, *not* a replacement for `ResultSet`. Tools mutate both per call; the agent only ever reads `ResultSet`, the React app only ever reads `UiState` via AG-UI shared state. See [`agent-compound-docs/decisions/frontend-stack.md`](../../agent-compound-docs/decisions/frontend-stack.md).
- **`ChatDeps` satisfies the AG-UI `StateHandler` protocol** by exposing a `state: UiState` dataclass field. The `AGUIAdapter` sets this from each incoming request and streams JSON Patch deltas of subsequent tool mutations back to the frontend.
- **Domain services take `db: Session` in the constructor** — framework-agnostic; works in FastAPI, scripts, and tests.
- **All cross-layer wiring goes through FastAPI `Depends`** in `core/dependencies.py`. No module-level singletons in the request path beyond the session store.

## Configuration

Values are read from environment variables (set via root `.env` or Docker Compose).

| Variable             | Description                                                                                                                                       | Default                            |
|----------------------|---------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------|
| `DATABASE_URL`       | PostgreSQL connection string                                                                                                                      | — (required)                       |
| `ANTHROPIC_API_KEY`  | Anthropic API key (native prompt caching)                                                                                                         | — (required)                       |
| `ANTHROPIC_MODEL`    | Anthropic model id (e.g. `claude-sonnet-4-6`, `claude-haiku-4-5`)                                                                                 | `claude-sonnet-4-6`                |
| `JINA_API_KEY`       | Jina embeddings API key (optional — empty disables semantic search)                                                                                | —                                  |
| `JINA_BASE_URL`      | Jina API base URL                                                                                                                                 | `https://api.jina.ai/v1`           |
| `PHOENIX_ENABLED`    | Enable Phoenix observability                                                                                                                      | `false`                            |
| `PHOENIX_ENDPOINT`   | Phoenix OTLP endpoint                                                                                                                             | `http://localhost:6006/v1/traces`  |
