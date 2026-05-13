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
├── main.py              # FastAPI app, middleware, router registration
├── core/
│   ├── config.py        # Pydantic Settings (env vars)
│   └── database.py      # SQLAlchemy engine, session, Base
├── api/
│   └── chat.py          # Thin FastAPI router for conversations
├── llm/
│   └── gateway.py       # LLM gateway (LiteLLM, BYOK, retries)
├── chat/
│   ├── service.py       # Chat business logic
│   └── schemas.py       # Pydantic request/response models
├── search/              # Search domain (future)
└── users/               # User domain (future)
alembic/                 # Database migrations
tests/                   # Test suite (pytest)
```

## Configuration

Values are read from environment variables (set via root `.env` or Docker Compose).

| Variable            | Description                  | Default                                                    |
|---------------------|------------------------------|------------------------------------------------------------|
| `DATABASE_URL`      | PostgreSQL connection string | `postgresql://flat_chat:flat_chat@localhost:5432/flat_chat` |
| `LLM_MODEL`        | LiteLLM model string         | `openrouter/openrouter/free`                               |
| `OPENROUTER_API_KEY`| OpenRouter API key           | —                                                          |
| `OPENAI_API_KEY`    | OpenAI API key (optional)    | —                                                          |
| `ANTHROPIC_API_KEY` | Anthropic API key (optional) | —                                                          |
| `LLM_NUM_RETRIES`  | Retry count for LLM calls    | `5`                                                        |
| `LLM_RETRY_AFTER`  | Min seconds between retries  | `5`                                                        |
