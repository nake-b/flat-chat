# CLAUDE.md

Project context for Claude Code.

## Project Overview

Berlin Apartment AI Assistant — a chatbot to help Berliners find apartments quickly and make informed decisions through conversational search.

## Tech Stack

- **Frontend:** React, Vite, TypeScript
- **Backend:** FastAPI, SQLAlchemy, Pydantic AI
- **Database:** PostgreSQL + pgvector + PostGIS (vector search, structured and geo data)
- **Infrastructure:** Nginx (reverse proxy), Docker, Docker Compose
- **Python:** 3.14 (uv + pyproject.toml for dependency management)
- **LLM:** Pydantic AI (native OpenRouter/Anthropic/Ollama support, agent tools, retries)

## Project Structure

```
services/frontend/          → React + Vite chat UI, served by Nginx as static files
services/backend/           → FastAPI app, domain-isolated layered architecture
  src/flat_chat/
    main.py                 → FastAPI app, router registration
    core/                   → Config (Pydantic Settings), database (engine, sessions)
    api/                    → Thin FastAPI routers (HTTP concerns only)
    chat/                   → Chat domain
                              agent.py    — Agent(toolsets=[toolset]), INSTRUCTIONS, run_agent()
                              tools.py    — FunctionToolset[ChatDeps]() + @toolset.tool
                              state.py    — ChatSession, ResultSet (central formatting), ChatDeps
                              sessions.py — SessionStore Protocol + InMemorySessionStore
                              service.py  — ChatService orchestration
                              schemas.py  — API request/response models
                              providers/  — chat-model dispatch (single provider seam)
                                __init__.py  — build_chat_model(settings)
                                openrouter.py
    search/                 → Search domain (service, models, schemas — SearchParams)
    users/                  → Users domain (sessions, bookmarks — future)
services/ingestion/         → Batch data ingestion, triggered by cron
nginx/                      → Reverse proxy config (serves static files at /, proxies /api/ → backend)
agent-compound-docs/        → Architecture decisions and deployment guide
```

## Running the Project

```bash
docker compose up --build        # Start all services at http://localhost
docker compose --profile ingestion run --rm ingestion   # Run ingestion manually
```

## API Conventions

- All API routes are prefixed with `/api/`
- Chat uses an app-level REST API (not OpenAI-style): `POST /api/conversations`, `POST /api/conversations/{id}/messages`
- The frontend uses relative URLs (`/api/...`) — works via both Vite dev proxy and Nginx
- Nginx only proxies `/api/conversations` and `/api/health` to the backend — no wildcard `/api/` exposure

## Architecture Notes

- Nginx is a separate Docker Compose service (not embedded in the frontend container)
- Only Nginx exposes a port (80) — all other services are internal
- PostgreSQL is defined in docker-compose.yml only (no dedicated directory)
- Backend package is `flat_chat` (not `app`) — run with `uvicorn flat_chat.main:app`
- Domain services take `db: Session` in constructor — framework-agnostic, works in FastAPI, scripts, and tests
- LLM uses Pydantic AI with `instructions=` (not `system_prompt=`), `FunctionToolset[ChatDeps]` for tools (no module-level cycle between `agent.py` and `tools.py`), `RunContext[ChatDeps]` for dependency injection
- Conversation state lives in `ChatSession` (history + active `ResultSet`), held by a `SessionStore` Protocol — `InMemorySessionStore` today, swap for DB-backed later
- `ResultSet` (in `chat/state.py`) owns every listing-formatting concern shown to the LLM — `summary` (prose top-N), `page` (CSV bulk), `detail` (prose full fields), `describe_for_instructions` (one-line state). Every list-style response ends with an explicit navigation footer. See `agent-compound-docs/decisions/llm-tool-result-design.md`.
- All cross-layer wiring goes through FastAPI `Depends` (`core/dependencies.py`) — no module-level singletons in the request path other than the session store
- LLM provider selection lives in `chat/providers/__init__.py:build_chat_model()` — the single provider seam. Add a provider by appending an `if settings.<provider>_api_key:` branch. When multiple keys are set, returns a `FallbackModel` chain (dev: multiple free providers; prod: usually one paid provider → no chain)
- Phoenix observability runs as a compose service in dev (UI at `http://localhost:6006`). `core/observability.py` wires `OpenInferenceSpanProcessor` + `Agent.instrument_all()` so every agent run / model request / tool call emits OTel spans. Enable per-env via `PHOENIX_ENABLED` (defaults to true in dev compose; off everywhere else)
- The architecture is evolving iteratively — question choices, suggest improvements, flag concerns

## agent-compound-docs/

Architecture decisions and guides live in `agent-compound-docs/`. When making significant architectural decisions, document them there with what was chosen, what was rejected, and why. Read existing docs before proposing changes to areas they cover.

## Architecture diagram

The architecture lives in `architecture.drawio` (source of truth, edit in draw.io Desktop or app.diagrams.net) and `architecture.png` (rendered output, regenerated via `./render.sh` which calls draw.io Desktop's CLI). **If asked to update or redo the diagram, edit the existing .drawio — do not start from scratch.** Layout, conventions, and the list of things the diagram must convey are documented in `agent-compound-docs/decisions/architecture-diagram.md` — read it first.

The .drawio file is ~900KB due to embedded SVG icons. **Read `agent-compound-docs/decisions/editing-drawio-programmatically.md` before editing** — it documents how to parse, modify, strip images for MCP preview, and verify horizontal line alignment via Python scripts.

## Keeping docs and env in sync

Drift between these files causes painful onboarding and stale review feedback. When you change anything in one of the buckets below, update the rest in the same change.

**Env vars** — when adding, renaming, removing, or changing a default:
- `services/backend/src/flat_chat/core/config.py` — the Pydantic Settings field (required fields use `Field(...)`)
- `.env.example` — placeholder value + one-line intent
- `docker-compose.yml` — the `environment:` block of the relevant service
- `services/backend/README.md` — the config table

(The user's `.env` is their own — never write to it. If a *required* var is missing there, flag it instead of guessing.)

**Architecture or surface area** — when adding/removing a module, route, dependency, or service:
- `CLAUDE.md` — `Project Structure`, `Architecture Notes`
- `README.md` (root) — tech stack, project structure
- `services/backend/README.md` — project layout, API endpoints
- `architecture.drawio` (then `./render.sh` to regenerate `architecture.png`)
- `agent-compound-docs/decisions/` — if it's a significant choice, capture what was rejected and why

When you finish a change, do a quick sweep: grep for the old name / removed file across the project so nothing references it stale.

## MVP Scope

- User describes apartment requirements to chatbot
- Results displayed on a map
- Iterative refinement through conversation

## Out of Scope

- Cities other than Berlin

## Pydantic AI Patterns

Install: `pip install "pydantic-ai"` (or `pip install "pydantic-ai[web]"` for FastAPI/AGUIAdapter support).

### Agent Definition

```python
from pydantic_ai import Agent, RunContext

# Use instructions= (canonical), not system_prompt=
agent = Agent(
    'openai:gpt-4o',
    deps_type=MyDeps,
    output_type=MyOutput,
    instructions="You are a helpful assistant.",  # static instructions
    tool_retries=3,                                # default retries for tool calls (use output_retries= for output validation)
)

# Dynamic instructions via decorator — multiple stack in order
@agent.instructions
def add_context(ctx: RunContext[MyDeps]) -> str:
    return f"User: {ctx.deps.user_name}. Today: {date.today()}."

@agent.instructions
def add_rules() -> str:
    return "Always respond in German."
```

### Model Configuration

```python
# Native provider prefixes — no LiteLLM needed
agent = Agent('openai:gpt-4o')
agent = Agent('anthropic:claude-sonnet-4-5')
agent = Agent('openrouter:anthropic/claude-sonnet-4-5')
agent = Agent('ollama:llama3.2')
agent = Agent('groq:llama-3.3-70b-versatile')
agent = Agent('mistral:mistral-large-latest')

# BYOK — construct model per-call
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterProvider
model = OpenRouterModel('anthropic/claude-sonnet-4-5',
                        provider=OpenRouterProvider(api_key=user_api_key))
result = await agent.run(prompt, model=model)

# FallbackModel for provider failover
from pydantic_ai.models.fallback import FallbackModel
model = FallbackModel('anthropic:claude-sonnet-4-5', 'openai:gpt-4o')

# Per-call overrides
from pydantic_ai.settings import ModelSettings
result = await agent.run(prompt, model_settings=ModelSettings(
    temperature=0.2, max_tokens=2000, timeout=30.0
))
```

### Dependencies

```python
from dataclasses import dataclass
from pydantic_ai import Agent, RunContext

@dataclass
class MyDeps:
    db: AsyncSession
    user_id: str
    http_client: httpx.AsyncClient

agent = Agent('openai:gpt-4o', deps_type=MyDeps)

# Access in tools and instructions via ctx.deps
@agent.tool
async def get_user_info(ctx: RunContext[MyDeps]) -> str:
    user = await ctx.deps.db.get(User, ctx.deps.user_id)
    return f"{user.name}, {user.email}"

# Deps are mutable — tools can modify ctx.deps and later tools see changes
result = await agent.run("Hello", deps=MyDeps(db=db, user_id="123", http_client=client))
```

### Tools

This project uses `FunctionToolset` so tools live in their own module without
ever importing the `agent` object — kills the `agent.py ↔ tools.py` cycle.

```python
# chat/tools.py
from pydantic_ai import FunctionToolset, RunContext
from flat_chat.chat.state import ChatDeps

toolset: FunctionToolset[ChatDeps] = FunctionToolset()

@toolset.tool
async def search_apartments(ctx: RunContext[ChatDeps], query: str) -> str: ...

# chat/agent.py
from flat_chat.chat.tools import toolset
agent = Agent(deps_type=ChatDeps, toolsets=[toolset], instructions=...)
```

Below is the older `@agent.tool` form — still valid for standalone agents,
but use `FunctionToolset` when tools live in a separate module:

```python
# @agent.tool — needs RunContext (access to deps)
@agent.tool
async def search_apartments(ctx: RunContext[MyDeps], query: str, max_price: int) -> str:
    """Search apartments matching criteria.

    Args:
        query: Natural language search query.
        max_price: Maximum monthly rent in euros.
    """
    # Parameters extracted from signature + docstring -> JSON schema for LLM
    results = await ctx.deps.db.execute(...)
    return str(results)

# @agent.tool_plain — standalone, no RunContext
@agent.tool_plain
def calculate_commute(origin: str, destination: str) -> str:
    """Calculate commute time between two Berlin addresses."""
    return "25 minutes by U-Bahn"

# Flat parameters preferred over nested objects for better LLM compatibility

# ModelRetry for retry with guidance
from pydantic_ai import ModelRetry

@agent.tool(retries=3)
async def lookup_address(ctx: RunContext[MyDeps], address: str) -> str:
    """Look up a Berlin address."""
    if not address.endswith(", Berlin"):
        raise ModelRetry("Address must include ', Berlin' suffix")
    return f"Found: {address}"

# ToolReturn for separating return_value, content, and metadata
from pydantic_ai import ToolReturn, BinaryContent

@agent.tool_plain
def capture_map(lat: float, lng: float) -> ToolReturn:
    """Capture a map screenshot at coordinates."""
    screenshot = BinaryContent(data=b'\x89PNG...', media_type='image/png')
    return ToolReturn(
        return_value=f"Map captured at ({lat}, {lng})",  # -> LLM as tool result
        content=["Map view:", screenshot],                # -> LLM as user message (images, etc.)
        metadata={"lat": lat, "lng": lng},                # -> app only, not sent to LLM
    )
```

### Retries

```python
# Agent-level retries — tool_retries= caps tool calls, output_retries= caps output validation
# (retries= is a deprecated alias that cascades to both)
agent = Agent('openai:gpt-4o', tool_retries=3)

# Output-specific retries
agent = Agent('openai:gpt-4o', output_type=MyModel, output_retries=5)

# Per-tool retries
@agent.tool(retries=3)
async def flaky_tool(ctx: RunContext[MyDeps], query: str) -> str: ...

# ModelRetry inside tools — message goes back to LLM as guidance
raise ModelRetry("Invalid format. Use ISO 8601 dates like 2024-01-15.")
```

### Structured Output

```python
from pydantic import BaseModel

class ApartmentResult(BaseModel):
    address: str
    price: int
    rooms: float
    summary: str

# Single type
agent = Agent('openai:gpt-4o', output_type=ApartmentResult)

# Union types — LLM picks the right one
class SearchResult(BaseModel): ...
class Clarification(BaseModel): ...
agent = Agent('openai:gpt-4o', output_type=SearchResult | Clarification)

# Validation failures trigger retries automatically
```

### Message History

```python
# After a run, get messages
result = await agent.run("Find apartments in Kreuzberg", deps=deps)
messages = result.all_messages()       # list[ModelMessage] — full conversation
new_msgs = result.new_messages()       # list[ModelMessage] — only this run

# Serialize to JSON bytes
json_bytes = result.all_messages_json()  # bytes
json_new = result.new_messages_json()    # bytes

# Deserialize
from pydantic_ai import ModelMessagesTypeAdapter
messages = ModelMessagesTypeAdapter.validate_json(json_bytes)
messages = ModelMessagesTypeAdapter.validate_python(raw_list)

# Continue conversation — pass history to next run
result2 = await agent.run("Under 1000 euros", deps=deps, message_history=messages)
```

### Embeddings

```python
from pydantic_ai.embeddings import Embedder

embedder = Embedder('openai:text-embedding-3-small')
vectors = await embedder.embed(['apartment in Kreuzberg', 'Wohnung mit Balkon'])
# vectors: list of float lists

# OpenAI-compatible providers
embedder = Embedder('openai:model-name', base_url='https://custom-endpoint.example.com/v1')
```

### Streaming

```python
async with agent.run_stream("Find apartments") as stream:
    async for event in stream:
        # DeltaThinkingPart for Claude thinking indicators
        print(event)
```

### FastAPI Integration (AGUIAdapter)

```python
from pydantic_ai.agui import AGUIAdapter

# One-liner SSE streaming endpoint — handles SSE, thinking, tool calls
@app.post("/api/chat")
async def chat(request: Request):
    return AGUIAdapter.dispatch_request(request, agent=agent)
```

### Testing

```python
from pydantic_ai.models.test import TestModel
from pydantic_ai.models.function import FunctionModel, AgentInfo

# Simple mock — returns canned response
with agent.override(model=TestModel(custom_output_text='Hello!')):
    result = await agent.run("test")
    assert result.output == "Hello!"

# Custom logic — full control over model behavior
async def my_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[TextPart("custom response")])

with agent.override(model=FunctionModel(my_model)):
    result = await agent.run("test")

# TestModel runs all registered tools by default, then returns result
```

### Usage Limits

```python
from pydantic_ai.usage import UsageLimits

limits = UsageLimits(
    request_limit=10,         # max LLM requests per run
    tool_calls_limit=5,       # max total tool calls
    total_tokens_limit=8000,  # max tokens (prompt + completion)
)
result = await agent.run(prompt, usage_limits=limits)
```
