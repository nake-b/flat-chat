# flat-chat backend

FastAPI backend for the Berlin Apartment AI chatbot.

## Setup

```bash
brew install just       # task runner (one-time)
uv sync                 # install all dependencies
```

## Running

```bash
just dev                # start uvicorn with reload
# or from project root:
docker compose up backend
```

## Quality Checks

```bash
just              # list all commands
just check        # run lint + typecheck + test
just lint         # ruff check
just typecheck    # ty check
just test         # pytest (accepts args: just test -k health)
just format       # ruff format
just fix          # auto-fix lint issues + format
```

## API Endpoints

| Endpoint                                  | Method | Description           |
|-------------------------------------------|--------|-----------------------|
| `/api/health`                             | GET    | Health check          |
| `/api/conversations`                      | POST   | Create a conversation |
| `/api/conversations/{id}/messages`        | POST   | Send a message        |
| `/api/conversations/{id}/messages`        | GET    | Get message history   |

## Project Layout

```
src/flat_chat/
├── main.py              # FastAPI app, lifespan, router registration
├── core/
│   ├── config.py        # Pydantic Settings (env vars)
│   ├── database.py      # SQLAlchemy engine, session, Base
│   └── observability.py # Phoenix/OpenTelemetry setup
├── api/
│   └── chat.py          # Thin FastAPI router for conversations
├── chat/
│   ├── agent.py         # Pydantic AI agent, deps, run_agent()
│   ├── service.py       # Chat business logic
│   ├── schemas.py       # Pydantic request/response models
│   └── tools.py         # Agent tools (search, details, pagination)
├── search/
│   ├── models.py        # Listing SQLAlchemy model
│   ├── schemas.py       # SearchFilters model
│   └── service.py       # SearchService (SQL + vector + geo)
└── users/               # User domain (future)
tests/                   # Test suite (pytest)
```

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
