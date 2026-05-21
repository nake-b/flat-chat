# Plan: ChatSession, Toolset Refactor, ResultSet Methods, CI

## Context

Cleanup pass on PR #4 (Pydantic AI agent + search service). Twenty-one review comments resolved into six overarching themes. The goal is not new functionality — it's making the existing surface clean, powerful, and persistent-ready.

Key decisions from review discussion:
- **`FunctionToolset`** to kill circular imports between `agent.py` and `tools.py`.
- **`ChatSession` + `SessionStore` Protocol** — in-memory now, DB-ready interface.
- **`ResultSet` becomes the central object** — owns all formatting, lives on the session.
- **Every result response includes a navigation footer** — model can reach #6, #7, etc.
- **Truthful sort labels** — never imply ranking that didn't happen.
- **FastAPI `Depends()`** as the single DI mechanism.
- **`SearchFilters` → `SearchParams`**.
- **CI**: ruff lint + ruff format check + `ty` type check + pytest.
- **No DB persistence in this PR** — Protocol is the bridge to the future PR.

Reference: `agent-compound-docs/decisions/llm-tool-result-design.md` distills the principles applied to `ResultSet`.

---

## Phase 1: Module restructure (kills circular imports)

New layout under `services/backend/src/flat_chat/chat/`:

```
state.py      — ChatSession, ResultSet (no agent imports)
sessions.py   — SessionStore Protocol + InMemorySessionStore
tools.py      — toolset = FunctionToolset[ChatDeps](); @toolset.tool …
agent.py      — Agent(toolsets=[toolset], …), ChatDeps, run_agent()
service.py    — ChatService.send_message()
schemas.py    — unchanged (API payload models)
```

Dependency direction is one-way: `state ← sessions, tools ← agent ← service`. No `if TYPE_CHECKING`. No lazy imports inside functions.

### `chat/state.py`
```python
@dataclass
class ResultSet:
    """Apartments currently under discussion in a session.
    Persists across messages; iterated by refining, paging, requesting details
    until small enough to decide on.
    """
    df: pd.DataFrame
    params: SearchParams

    @property
    def total(self) -> int: return len(self.df)

    def order_label(self) -> str: ...
    def summary(self, top_n: int = 5) -> str: ...
    def page(self, page: int, page_size: int = 10) -> str: ...
    def detail(self, indices: list[int]) -> str: ...
    def describe_for_instructions(self) -> str: ...

    def _format_row_prose(self, row, idx: int) -> str: ...
    def _format_row_csv(self, row, idx: int) -> str: ...
    def _navigation_footer(self, shown_end: int) -> str: ...

@dataclass
class ChatSession:
    id: str
    message_history: list[ModelMessage]
    result_set: ResultSet | None
    created_at: datetime
```

### `chat/sessions.py`
```python
class SessionStore(Protocol):
    def create(self) -> ChatSession: ...
    def get(self, session_id: str) -> ChatSession: ...   # raises 404-equivalent
    def save(self, session: ChatSession) -> None: ...

class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, ChatSession] = {}
    # implements the protocol
```

### `chat/tools.py`
```python
toolset = FunctionToolset[ChatDeps]()

@toolset.tool
async def search_apartments(ctx: RunContext[ChatDeps], …) -> str:
    params = SearchParams(…)
    df = await ctx.deps.search_service.search(params)
    ctx.deps.session.result_set = ResultSet(df=df, params=params)
    return ctx.deps.session.result_set.summary()

@toolset.tool
async def get_result_details(ctx: RunContext[ChatDeps], indices: list[int]) -> str:
    rs = ctx.deps.session.result_set
    if not rs: return "No active search results. Run a search first."
    return rs.detail(indices)

@toolset.tool
async def get_result_page(ctx: RunContext[ChatDeps], page: int = 1, page_size: int = 10) -> str:
    rs = ctx.deps.session.result_set
    if not rs: return "No active search results. Run a search first."
    return rs.page(page, page_size)
```

### `chat/agent.py`
```python
@dataclass
class ChatDeps:
    db: DbSession                       # SQLAlchemy
    search_service: SearchService
    session: ChatSession                # ours

INSTRUCTIONS = """You are a helpful Berlin apartment search assistant. …"""

agent = Agent(
    deps_type=ChatDeps,
    toolsets=[toolset],
    instructions=INSTRUCTIONS,
    tool_retries=3,
)

@agent.instructions
def add_result_context(ctx: RunContext[ChatDeps]) -> str:
    rs = ctx.deps.session.result_set
    return rs.describe_for_instructions() if rs else "No active search results."

def _get_model() -> str:
    from flat_chat.core.config import settings
    return f"openrouter:{settings.llm_model}"

async def run_agent(user_message: str, deps: ChatDeps) -> AgentResult:
    result = await agent.run(
        user_message,
        model=_get_model(),
        deps=deps,
        message_history=deps.session.message_history,
    )
    return AgentResult(output=result.output, new_messages=result.new_messages())
```

`tools.py` no longer imports `agent`; `agent.py` imports `toolset` from `tools.py`. The cycle is gone. `tool_retries=3` instead of the deprecated `retries=` (verify against current Pydantic AI version during implementation).

---

## Phase 2: Service + router rewire (DI cleanup)

### `chat/service.py`
```python
class ChatService:
    def __init__(self, db, search_service, store):
        self.db = db
        self.search_service = search_service
        self.store = store

    async def send_message(self, session_id: str, content: str) -> str:
        session = self.store.get(session_id)
        deps = ChatDeps(db=self.db, search_service=self.search_service, session=session)
        result = await run_agent(content, deps)
        session.message_history.extend(result.new_messages)
        self.store.save(session)
        return result.output
```

### `core/dependencies.py` (new)
```python
def get_session_store() -> SessionStore:
    return _store_singleton   # process-lifetime in-memory

def get_search_service(db = Depends(get_db), embedder = Depends(get_embedder)) -> SearchService:
    return SearchService(db, embedder)

def get_chat_service(
    db = Depends(get_db),
    search_service = Depends(get_search_service),
    store = Depends(get_session_store),
) -> ChatService:
    return ChatService(db, search_service, store)
```

### `api/chat.py`
- Drop module-level `conversations` dict.
- `POST /conversations` → `store.create()` → return id + created_at.
- `POST /conversations/{id}/messages` → `chat.send_message(id, body.content)`.
- `GET /conversations/{id}/messages` → reconstruct via store, iterate `ModelMessage`. Same parsing logic as today but moved into a helper inside the router or onto `ChatSession` (lean: helper in router for now — `ChatSession` shouldn't know about the API response shape).

---

## Phase 3: Naming + small fixes

- `SearchFilters` → `SearchParams`. Single rename across `search/schemas.py`, `search/service.py`, `chat/tools.py`, `chat/state.py`.
- `ResultSet.total` → property, drop the field.
- Add docstrings to `ChatSession`, `ResultSet`, `ChatDeps`.
- Move instructions to a module-level `INSTRUCTIONS` constant in `agent.py` (or pull to its own file if it grows).
- Reorder `agent.py`: imports → constants → dataclasses → agent → dynamic-instruction functions → run helpers.

---

## Phase 4: CI workflow

`.github/workflows/ci.yml` — backend job:
- `uv sync` (or equivalent for project setup)
- `uv run ruff check src tests`
- `uv run ruff format --check src tests`
- `uv run ty check src` (Astral's type checker — fast, ruff-style)
- `uv run pytest`

Open question for implementation time: does `ty` need any config (`ty.toml` / `pyproject.toml` section)? Default settings are usually fine to start. If it surfaces too much existing noise, narrow the path or add per-file ignores rather than disabling rules wholesale.

Trigger: `push` and `pull_request` to `main`. Concurrency group to cancel superseded runs.

---

## Phase 5: Verification

- `docker compose up --build` — full local stack still works end-to-end.
- Create a conversation, run a search ("apartments in Kreuzberg under 1200"), verify:
  - Sort label says "sorted by relevance" (with query) or "most recent first" (without).
  - Footer lists `get_result_page` and `get_result_details` with example arg shapes.
  - LLM successfully fetches page 2.
  - LLM successfully fetches detail for indices.
- Second user message in same conversation: `session.result_set` still present, agent instructions reflect it.
- CI green on a draft PR.

---

## File matrix

| File | Action |
|---|---|
| `chat/state.py` | **Create** — `ChatSession`, `ResultSet` |
| `chat/sessions.py` | **Create** — `SessionStore` Protocol, `InMemorySessionStore` |
| `chat/tools.py` | **Rewrite** — `FunctionToolset`, delegates to `ResultSet` |
| `chat/agent.py` | **Rewrite** — `toolsets=[…]`, no lazy imports, `INSTRUCTIONS` const |
| `chat/service.py` | **Modify** — takes `store`, builds `ChatDeps`, persists session |
| `api/chat.py` | **Modify** — store-backed, no module dict |
| `core/dependencies.py` | **Create** — `Depends` factories |
| `search/schemas.py` | **Modify** — `SearchFilters` → `SearchParams` |
| `search/service.py` | **Modify** — rename references |
| `.github/workflows/ci.yml` | **Create** |
| `pyproject.toml` | **Modify** — add `ty` to dev deps if needed |
| `CLAUDE.md` | **Modify** — update Pydantic AI patterns section (toolset, session) |

## Key decisions

1. **`FunctionToolset`** removes the agent↔tools cycle structurally.
2. **`ChatSession` + `SessionStore` Protocol** — module-level dict gone, DB swap is a single new impl class.
3. **`ResultSet` owns formatting** — `summary` / `page` / `detail` / `describe_for_instructions`, with `_format_row_*` as the one place row layout lives.
4. **Navigation footer is mandatory** on every list-style response.
5. **Truthful order labels** — see decision doc.
6. **FastAPI `Depends`** end to end; `ChatDeps` is the runtime bridge between request-scoped services and session state.
7. **Prose for narratives, CSV for bulk** — `summary`/`detail` prose, `page` CSV body.
8. **`ty` type-checker** for CI — Astral's fast checker, consistent with ruff.

## Future (not in this plan)

- `PgSessionStore` — DB-backed `SessionStore` implementation. Drop-in.
- Streaming (`AGUIAdapter`).
- Phoenix observability.
- `geocode_location` and `get_travel_time` tools.
