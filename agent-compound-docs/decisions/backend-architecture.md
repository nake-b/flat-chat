# Backend Architecture: Directory Structure & LLM Gateway

Decided 2025-05-09.

## Context

The backend needs to support three distinct domains:
1. **User-facing backend (BFF)** — sessions, bookmarks, user preferences
2. **Chat + agent** — conversation management, LLM orchestration, tool calling
3. **Search** — vector search, geo search, apartment data

FastAPI is "just the API layer" — not a first-class citizen. The chat domain may also be exposed to external users via a separate API, and must be runnable from scripts/CLI/tests without FastAPI.

Future needs include: agent harness, tool calling, Google Maps integration, vector search, geo search, context engineering, and BYOK (bring your own key).

## Decision: Domain-Isolated Layered Structure

### Package name: `flat_chat` (not `app`)

`app` is generic and collides with the FastAPI `app` object. The project has a name — use it.

### Directory structure

```
src/flat_chat/
├── main.py                   → FastAPI app, router registration
├── core/
│   ├── config.py             → Pydantic Settings (all env vars)
│   └── database.py           → engine, SessionLocal, Base, get_db
├── api/                      → Thin FastAPI routers (HTTP concerns only)
│   ├── chat.py
│   ├── users.py
│   └── search.py
├── llm/
│   └── gateway.py            → Model factory, BYOK, provider routing, embeddings
├── users/
│   ├── service.py            → Business logic
│   ├── models.py             → SQLAlchemy ORM
│   └── schemas.py            → Pydantic request/response DTOs
├── chat/
│   ├── service.py            → Conversation orchestration
│   ├── agent.py              → Agent definition, tool wiring
│   ├── tools.py              → Bridges chat → search domain
│   ├── models.py
│   └── schemas.py
├── search/
│   ├── service.py            → Vector search, geo search, filters
│   ├── models.py
│   └── schemas.py
```

### Layer responsibilities

- **`api/`** — Thin. Parses HTTP requests, calls services, returns responses. Only layer that knows about FastAPI.
- **`{domain}/service.py`** — Business logic. Takes a `db: Session` in constructor. Does NOT know about FastAPI or `Depends()`.
- **`{domain}/models.py`** — SQLAlchemy ORM classes. Defines DB table shape.
- **`{domain}/schemas.py`** — Pydantic classes. Defines API request/response shape. Diverges from models (e.g., no embedding vectors in API responses).
- **`core/`** — Cross-cutting infrastructure. Config, database engine, session factory.
- **`llm/`** — LLM gateway. At root level because both `chat/` (completions) and `search/` (embeddings) use it.

### Dependency flow (one-directional, no circular imports)

```
api/*  →  {domain}/service  →  {domain}/models
                             →  llm/gateway
                             →  other domain's service (e.g., chat → search)
                             ↑
                   core/ (config, database)
```

### Database session pattern

Services take `db: Session` as a constructor argument. The caller provides the session:
- **FastAPI**: via `Depends(get_db)` in the router
- **Scripts/CLI**: via `SessionLocal()` with try/finally
- **Tests**: via a pytest fixture with rollback

This keeps services framework-agnostic — no extra layer needed.

## Rejected alternatives

### `app` as package name
Generic, collides with FastAPI `app` object.

### `llm/` inside `chat/`
Initially considered since only chat uses completions. But search needs embeddings from the same gateway, so it belongs at root.

### `dependencies.py` in core
Premature — no shared FastAPI dependencies exist yet (no auth). Add when needed.

### `__main__.py`
Not needed yet. Uvicorn command in docker-compose handles startup. Add later if a CLI entrypoint is wanted.

### Feature-based structure from the start
(Each domain has its own router inside the domain package.) Considered but rejected — having `api/` separate from domain logic makes the "FastAPI is just the API" principle explicit and makes it easier to add a second API surface later.

## Decision: LLM Gateway via LiteLLM

### What

Use LiteLLM as a Python library (not a proxy service) for provider abstraction, routing, and BYOK.

### Why LiteLLM

- **BYOK**: First-class per-request `api_key` passing. Critical for allowing users to bring their own keys.
- **Custom endpoints**: `api_base="http://localhost:8080"` — any OpenAI-compatible server works. No Ollama required.
- **Provider abstraction**: 100+ providers via model prefix routing (`openai/gpt-4o`, `anthropic/claude-sonnet-4-20250514`).
- **Free and self-hosted**: No SaaS dependency or recurring costs.
- **Pydantic AI compatibility**: Pydantic AI ships with `LiteLLMProvider`, so if/when we adopt Pydantic AI as the agent framework, LiteLLM slots in as its backend.
- **Embeddings**: Supports embedding models too, so `search/` can use the same gateway.

### Why not Pydantic AI as gateway

Pydantic AI is an agent framework that *includes* provider abstraction, but BYOK requires constructing a new model instance per request — clunky. LiteLLM handles this natively. The two are complementary: LiteLLM for gateway, Pydantic AI (later) for agent orchestration on top.

### Gateway module (`llm/gateway.py`)

A factory that returns configured LLM calls:
- Routes to the right provider based on model string
- Handles BYOK by accepting optional `api_key` per call
- Supports custom `api_base` for localhost/self-hosted models
- Used by `chat/` for completions and `search/` for embeddings
