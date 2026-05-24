# CLAUDE.md

Project context for Claude Code.

## Project Overview

Berlin Apartment AI Assistant — a chatbot to help Berliners find apartments quickly and make informed decisions through conversational search.

## Tech Stack

- **Frontend:** React, Vite, TypeScript, Tailwind, CopilotKit (AG-UI), MapLibre GL JS v5
- **Backend:** FastAPI, SQLAlchemy, Pydantic AI (with AG-UI Protocol adapter)
- **Database:** PostgreSQL + pgvector + PostGIS (vector search, structured and geo data)
- **Infrastructure:** Nginx (reverse proxy), Docker, Docker Compose
- **Python:** 3.14 (uv + pyproject.toml for dependency management)
- **LLM:** Pydantic AI (native OpenRouter/Anthropic/Ollama support, agent tools, retries)

## Project Structure

```
services/frontend/          → React + Vite chat-host SPA, served by Nginx as static files
  src/
    main.tsx                → bootstraps session, mounts <CopilotKit> with HttpAgent → /api/agent
    App.tsx                 → chat-host layout: chat left ~40%, map+cards right (Option-X resize)
    state/UiState.ts        → TS mirror of backend UiState (kept in sync manually)
    hooks/                  → useUiState (wraps useCoAgent), useHover (zustand, client-local)
    api/session.ts          → POST /api/conversations to allocate a thread_id
    components/             → ChatPane, MapPane (MapLibre + clustering), CardsPane / CardStrip / CardDetail
services/backend/           → FastAPI app, domain-isolated layered architecture
  src/flat_chat/
    main.py                 → FastAPI app, router registration
    core/                   → Config (Pydantic Settings), database (engine, sessions)
    api/                    → Thin FastAPI routers (HTTP concerns only)
                              chat.py     — POST create conversation, GET history (no message-send)
                              agent.py    — POST /api/agent — AG-UI streaming via AGUIAdapter
    chat/                   → Chat domain
                              agent.py    — Agent(toolsets=[toolset]), INSTRUCTIONS, dynamic instructions
                              tools.py    — FunctionToolset[ChatDeps]() + @toolset.tool (mirrors into UiState)
                              state.py    — ChatSession, ResultSet (LLM-facing), ChatDeps (StateHandler protocol)
                              ui_state.py — UiState / UiApartment (frontend mirror)
                              sessions.py — SessionStore Protocol + InMemorySessionStore
                              service.py  — ChatService — dispatches AG-UI run + persists state/history
                              schemas.py  — API response models
                              providers/  — chat-model dispatch (single provider seam)
                                __init__.py  — build_chat_model() orchestrator (key-presence only)
                                anthropic.py — direct Anthropic + prompt caching
                                openrouter.py — OpenRouter + body-error retry subclass
    search/                 → Search domain (service, models, schemas — SearchParams)
    users/                  → Users domain (sessions, bookmarks — future)
services/ingestion/         → Batch data ingestion, triggered by cron
nginx/                      → Reverse proxy (SPA at /, proxies /api/conversations + /api/agent (SSE), serves /tiles/)
data/tiles/                 → Protomaps .pmtiles for MapLibre — mounted into nginx at /tiles/
agent-compound-docs/        → Architecture decisions and deployment guide
```

## Running the Project

```bash
docker compose up --build        # Start all services at http://localhost
docker compose --profile ingestion run --rm ingestion   # Run ingestion manually
```

## API Conventions

- All API routes are prefixed with `/api/`
- Conversation lifecycle: `POST /api/conversations` (create session, id doubles as AG-UI `thread_id`), `GET /api/conversations/{id}/messages` (history reload — read-only)
- Sending a message goes through `POST /api/agent` — AG-UI Protocol streaming (SSE). The legacy `POST /api/conversations/{id}/messages` REST endpoint was removed
- The frontend uses relative URLs (`/api/...`) — works via both Vite dev proxy and Nginx
- Nginx proxies `/api/conversations`, `/api/agent` (with SSE-safe `proxy_buffering off`), `/api/health`, and serves `/tiles/` (Protomaps `.pmtiles` for MapLibre). No wildcard `/api/` exposure

## Architecture Notes

- Nginx is a separate Docker Compose service (not embedded in the frontend container)
- Only Nginx exposes a port (80) — all other services are internal
- PostgreSQL is defined in docker-compose.yml only (no dedicated directory)
- Backend package is `flat_chat` (not `app`) — run with `uvicorn flat_chat.main:app`
- Domain services take `db: Session` in constructor — framework-agnostic, works in FastAPI, scripts, and tests
- LLM uses Pydantic AI with `instructions=` (not `system_prompt=`), `FunctionToolset[ChatDeps]` for tools (no module-level cycle between `agent.py` and `tools.py`), `RunContext[ChatDeps]` for dependency injection
- Conversation state lives in `ChatSession` (history + `ResultSet` + `UiState`), held by a `SessionStore` Protocol — `InMemorySessionStore` today, swap for DB-backed later
- `ResultSet` (in `chat/state.py`) owns every listing-formatting concern shown to the LLM — `summary` (prose top-N), `page` (CSV bulk), `detail` (prose full fields), `describe_for_instructions` (one-line state). Every list-style response ends with an explicit navigation footer. See `agent-compound-docs/decisions/llm-tool-result-design.md`.
- `UiState` (in `chat/ui_state.py`) is the parallel frontend mirror — a Pydantic model of typed apartments + `active_id` + `tool_logs`. `ChatDeps` exposes it as a `state: UiState` dataclass field so it satisfies the Pydantic AI `StateHandler` protocol; `AGUIAdapter` sets it per request from the AG-UI envelope. Tools mutate both `ResultSet` (LLM-facing prose) and `state` (UI-facing structured data) on every call.
- **State events are not auto-emitted** by Pydantic AI's AG-UI adapter — `deps.state` mutations alone are invisible to the frontend. To push state to the UI, tools must return `ToolReturn(return_value=…, metadata=[StateSnapshotEvent(snapshot=state.model_dump())])`. The adapter yields any `BaseEvent` in `ToolReturn.metadata` into the SSE stream alongside the regular `TOOL_CALL_RESULT`. See the `_return_with_state` helper in `chat/tools.py` and `agent-compound-docs/decisions/frontend-stack.md`.
- All cross-layer wiring goes through FastAPI `Depends` (`core/dependencies.py`) — no module-level singletons in the request path other than the session store
- LLM provider selection lives in `chat/providers/__init__.py:build_chat_model()` — the single provider seam. The orchestrator only checks key presence; each `build_<provider>_model()` builder owns its own validation and any provider-specific model settings (e.g. Anthropic cache breakpoints live in `providers/anthropic.py`, not on the Agent). When multiple keys are set, returns a `FallbackModel` chain — Anthropic preferred so prompt caching applies, OpenRouter as failover. See the docstring in `providers/__init__.py` for the four-layer rule and the "add a provider" recipe
- Phoenix observability runs as a compose service in dev (UI at `http://localhost:6006`, SQLite persisted to the `phoenix_data` volume). `core/observability.py` uses `phoenix.otel.register(batch=True)` for the OTel pipeline (BatchSpanProcessor + OTLP/HTTP exporter), then attaches `OpenInferenceSpanProcessor` explicitly (the package ships only a SpanProcessor, no Instrumentor — `auto_instrument` can't find it), and calls `Agent.instrument_all()` for Pydantic AI's native span emission. Per-conversation grouping comes from `with using_session(session_id)` around the agent run in `chat/service.py`. Enable per-env via `PHOENIX_ENABLED` (defaults to true in dev compose; off everywhere else)
- **No side effects at module import.** Process-wide setup (observability, connection pools, HTTPX clients, model warm-up) goes in the FastAPI `lifespan` context manager in `main.py`, with a paired teardown after `yield` if the resource needs flushing/closing. Module-level calls on import are surprising for tests and scripts that import `flat_chat.main` for non-serving purposes
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
- Mobile / responsive layouts — desktop-only product. Don't add mobile breakpoints, bottom sheets, or touch-first interactions unless the user reverses this decision.

## Deferred / nice-to-have (post-MVP)

- **Agent-callable frontend tools** (AG-UI Generative-UI pattern 3) — e.g. `pan_map_to(lat, lng)`, `expand_card(id)`, `highlight_kiez(name)` exposed via CopilotKit's `useCopilotAction` so the agent can drive the UI directly instead of only via shared state. Worth revisiting once the chat ↔ map shared-state loop is solid; powerful for things like "zoom to where I'm looking" or guided tours.
- **Pricing pins** — replace the plain circle marker on the map with HTML/SVG pins that display the warm-rent number. Turns the map into a free price-density visualization at zoom-out and lets clusters report a price range instead of just a count.
- **Filter UI** — none for MVP (conversational thesis). If user testing surfaces real friction with sliders/checkboxes, add a slim sort/filter bar above the card strip — keep it secondary to chat, never above.
- **Self-hosted Protomaps Berlin tiles** — nginx and the `data/tiles/` volume are already wired (`/tiles/` location with Range + CORS). Drop a `berlin.pmtiles` extract in (see `data/tiles/README.md`) and swap the demo style URL in `MapPane.tsx` to switch off the CartoCDN demo style.
- **Pydantic → TypeScript codegen** for `UiState`. Manual sync today; add `pydantic-to-typescript` or a small in-repo codegen if drift between `chat/ui_state.py` and `state/UiState.ts` starts costing time.
- **Surface active search filters in the UI.** Today the tool-call status pill summarizes only the count + district to stay short. Eventually the user should see the *full* filter set (price range, rooms, area, etc.) so they can verify their constraints and remove/edit one without re-typing the whole query. Likely a slim chip row below the chat header, or above the card strip — chips with `× remove` affordance that mutate `UiState` and re-trigger search. Captures the value of a filter UI without abandoning the conversational thesis.
- **Agent-driven map navigation.** When the user says "show me Kreuzberg" or "zoom out to all of Berlin" the agent should pan/zoom the map — not just filter results. Implement as an AG-UI frontend tool (`pan_map_to_district(name)` / `fit_map_to_results()`) exposed via CopilotKit's `useCopilotAction`, called by the agent like any other tool. Pairs naturally with the broader Generative-UI pattern-3 item above and lets the agent treat the map as an output device, not just a passive view.

## Pydantic AI Patterns

Install: `pip install "pydantic-ai" "pydantic-ai-slim[ag-ui]"` — the AG-UI extra is on `pydantic-ai-slim`, not the meta package; install both to get all provider extras plus the `AGUIAdapter` for our `/api/agent` route.

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
# Adapter lives at pydantic_ai.ui.ag_ui — the old top-level path is gone.
from pydantic_ai.ui.ag_ui import AGUIAdapter
from pydantic_ai.ui import StateHandler  # protocol that ChatDeps satisfies

@app.post("/api/agent")
async def run_agent(request: Request):
    return await AGUIAdapter.dispatch_request(
        request, agent=agent, deps=deps, on_complete=persist_session
    )
```

The adapter parses the AG-UI envelope (`thread_id`, `state`, `messages`),
sets `deps.state` via setter (no `dataclasses.replace`), runs the agent,
and streams text deltas + tool-call lifecycle + JSON Patch state deltas
back over SSE. `on_complete(result: AgentRunResult)` is the persistence hook.

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
