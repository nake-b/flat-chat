# flat-chat backend

FastAPI backend for the Berlin Apartment AI chatbot.

## Setup

```bash
uv sync          # install all dependencies (including dev tools)
```

## Running

From the project root (starts postgres automatically):

```bash
docker compose up backend
```

## Quality Checks

```bash
uv run ruff check .       # lint — find problems
uv run ruff check --fix . # lint — auto-fix what it can
uv run ruff format .      # format code
uv run ty check           # type check
uv run pytest             # run tests
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
src/app/
├── main.py              # FastAPI app, middleware, router registration
├── config.py            # pydantic-settings config (reads from env / .env)
├── database.py          # SQLAlchemy engine, session, Base
├── schemas.py           # Pydantic request/response models
└── routers/
    └── conversations.py # Chat conversation endpoints
alembic/                 # Database migration infrastructure
tests/                   # Test suite (pytest)
```

## Configuration

Configuration is managed via `pydantic-settings`. Values are read from environment variables (set by Docker Compose via the root `.env`).

| Variable       | Description                  | Default                                                    |
|----------------|------------------------------|------------------------------------------------------------|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://flat_chat:flat_chat@localhost:5432/flat_chat` |
