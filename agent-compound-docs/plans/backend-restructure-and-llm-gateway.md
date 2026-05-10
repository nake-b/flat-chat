# Plan: Backend Restructure + LLM Gateway

## Goal

Restructure the backend from flat `src/app/` to domain-isolated `src/flat_chat/`, and integrate LiteLLM as the LLM gateway. After this, the dummy chatbot becomes a real LLM-powered chatbot.

## Step 1: Rename package `app` → `flat_chat`

- Rename `services/backend/src/app/` → `services/backend/src/flat_chat/`
- Update `pyproject.toml`: hatch build target `src/app` → `src/flat_chat`
- Update `pyproject.toml`: ruff `src = ["src"]` stays the same
- Update `alembic/env.py`: change `from app.config` → `from flat_chat.core.config`
- Update `docker-compose.yml`: uvicorn command `app.main:app` → `flat_chat.main:app`
- Update `Dockerfile`: CMD uses `flat_chat.main:app`
- Update deployment guide in compound-docs

## Step 2: Create directory structure

```
src/flat_chat/
├── __init__.py
├── main.py
├── core/
│   ├── __init__.py
│   ├── config.py
│   └── database.py
├── api/
│   ├── __init__.py
│   └── chat.py
├── llm/
│   ├── __init__.py
│   └── gateway.py
├── chat/
│   ├── __init__.py
│   ├── service.py
│   ├── models.py
│   └── schemas.py
├── search/
│   ├── __init__.py
│   ├── service.py
│   ├── models.py
│   └── schemas.py
├── users/
│   ├── __init__.py
│   (empty for now — service.py, models.py, schemas.py added when needed)
```

Note: `users/` gets created as a placeholder with just `__init__.py`. No premature code.

## Step 3: Move and refactor existing code

### `core/config.py`
- Move from `app/config.py`
- Add LLM settings: `llm_model`, `llm_api_key`, `llm_temperature`, `llm_max_tokens`

### `core/database.py`
- Move from `app/database.py`
- No changes — already has engine, SessionLocal, Base, get_db

### `chat/schemas.py`
- Move from `app/schemas.py`
- Same content: `ConversationResponse`, `MessageCreate`, `MessageResponse`

### `chat/models.py`
- Empty for now (conversations still in-memory). DB models are separate future work.

### `api/chat.py`
- Move from `app/routers/conversations.py`
- Refactor: make `send_message` async, delegate to `ChatService`
- Keep in-memory conversation storage here temporarily (move to ChatService + DB later)

### `main.py`
- Move from `app/main.py`
- Update imports: `from flat_chat.api import chat`
- Keep CORS middleware, health endpoint

## Step 4: Add LiteLLM

### `pyproject.toml`
- Add `"litellm>=1.30"` to dependencies
- Run `uv lock`

### `llm/gateway.py`
- `async def get_completion(messages, api_key=None) -> str`
  - Calls `litellm.acompletion()` with settings from config
  - Accepts optional `api_key` for BYOK
- `SYSTEM_PROMPT` constant — Berlin apartment search assistant persona

### `chat/service.py`
- `ChatService.__init__(self, db: Session)` (db unused for now, ready for later)
- `async def send_message(self, conversation_id, content, history) -> str`
  - Builds message list from history
  - Calls `llm.gateway.get_completion()`
  - Returns assistant response text

### `api/chat.py`
- `send_message` endpoint becomes `async def`
- Builds history from in-memory store
- Calls `ChatService.send_message()`
- Catches exceptions, returns graceful fallback

## Step 5: Update Docker Compose + env

### `docker-compose.yml`
- Update uvicorn command to `flat_chat.main:app`
- Add env vars: `LLM_MODEL`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`

### `.env.example`
- Add LLM config section with comments

### `.env`
- Add placeholder LLM vars

## Step 6: Update deployment guide

- Update `agent-compound-docs/decisions/deployment-guide.md` with new uvicorn command

## Step 7: Verify

1. `cd services/backend && uv sync` — confirm litellm installs
2. `uv run uvicorn flat_chat.main:app --reload` — app starts
3. `curl POST /api/health` — health check works
4. `curl POST /api/conversations` → create conversation
5. `curl POST /api/conversations/{id}/messages` → get real LLM response
6. `curl POST` follow-up message → verify history maintained
7. Test with invalid API key → graceful error message
8. `docker compose up --build` — full stack works

## Risks

- **Python 3.14 + LiteLLM**: litellm or transitive deps may lack 3.14 wheels. Fallback: relax `requires-python` to `>=3.12`.
- **Import path changes**: All imports change from `app.*` to `flat_chat.*`. Must update alembic, docker, tests.
