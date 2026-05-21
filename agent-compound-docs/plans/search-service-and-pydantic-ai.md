# Plan: Search Service + Pydantic AI Agent + Observability (v2)

## Context

The backend has a working chat loop (LiteLLM gateway → in-memory conversations) and 126 apartment listings in Postgres with pgvector embeddings and PostGIS. We're replacing the entire LLM layer with Pydantic AI, building a search service, and adding observability.

Key decisions from discussion:
- **Drop LiteLLM** — Pydantic AI talks to providers natively (OpenRouter, Anthropic, Ollama)
- **Use `instructions=`** not `system_prompt=` (Pydantic AI canonical pattern)
- **Use Pydantic AI `Embedder`** for embeddings (Jina via OpenAI-compatible endpoint)
- **Use `AGUIAdapter`** for FastAPI streaming (SSE, thinking indicators built-in)
- **No separate `/api/search` endpoint** — search only accessible through agent tools
- **One search tool** — search and refine are the same function (re-search with merged filters)
- **Use GeoAlchemy2** for spatial queries
- **Use pandas** for in-memory result set management
- **Embeddings live inside SearchService** — caller passes text, service handles embedding
- **Phoenix** for observability via OpenTelemetry
- **Built-in retries** — `Agent(..., retries=3)` + `ModelRetry`, no tenacity

---

## Phase 0+1: Swap LiteLLM → Pydantic AI

### Dependencies (`pyproject.toml`)
- **Remove:** `litellm>=1.30`
- **Add:** `pydantic-ai[web]` (includes openrouter, anthropic, streaming, AGUIAdapter)
- **Add:** `pandas>=2.0`
- **Add:** `geoalchemy2>=0.17`
- **Move** `httpx>=0.28` from dev to main deps (needed for embeddings)

### Config (`core/config.py`)
- **Remove:** `llm_temperature`, `llm_max_tokens`, `llm_num_retries`, `llm_retry_after`
- **Keep:** `llm_model` (default: `google/gemma-4-31b-it:free`), `llm_api_key`
- **Add:** `llm_base_url: str = "https://openrouter.ai/api/v1"`
- **Add:** `jina_api_key: str = ""`
- **Add:** `jina_base_url: str = "https://api.jina.ai/v1"`

### Delete: `llm/gateway.py`

### Create: `chat/agent.py`
```python
@dataclass
class ChatDeps:
    db: Session
    search_service: SearchService      # injected, not constructed inside
    result_set: ResultSet | None = None  # mutable state for tool calls

@dataclass
class ResultSet:
    df: pd.DataFrame                   # full results as DataFrame
    filters: SearchFilters             # filters that produced this set
    total: int

agent = Agent(
    'openrouter:google/gemma-4-31b-it:free',
    deps_type=ChatDeps,
    instructions="You are a Berlin apartment search assistant...",
    retries=3,
)

@agent.instructions
def add_result_context(ctx: RunContext[ChatDeps]) -> str:
    if ctx.deps.result_set:
        return f"Current result set: {ctx.deps.result_set.total} listings. Filters: {ctx.deps.result_set.filters}"
    return "No active search results."
```
- Model built per-call using `OpenRouterModel` with settings from config
- Export: `async def run_agent(user_message, message_history, deps) -> AgentRunResult`
- No `chat/history.py` needed — Pydantic AI handles message serialization natively

### Modify: `chat/service.py`
- Constructor takes `db: Session`
- `send_message()` builds deps (with SearchService), calls `run_agent()`
- Returns agent result text

### Modify: `api/chat.py`
- Use `AGUIAdapter.dispatch_request(request, agent=agent)` for streaming SSE
- Or keep existing REST pattern for now and add AGUIAdapter later
- Inject `db: Session = Depends(get_db)`

### Docker (`docker-compose.yml`, `.env.example`)
- Remove `LLM_NUM_RETRIES`, `LLM_RETRY_AFTER`
- Change `LLM_MODEL` default to `google/gemma-4-31b-it:free`
- Add `LLM_BASE_URL`, `JINA_API_KEY`, `JINA_BASE_URL`

### Update: `CLAUDE.md`
- Tech Stack: replace LiteLLM with Pydantic AI, remove Alembic
- Architecture Notes: remove LiteLLM and Alembic references

### Verify
- `uv lock && just check`
- Send a message, get a response from OpenRouter
- Test with `TestModel`

---

## Phase 2: SearchService

### Create: `search/schemas.py`
```python
class SearchFilters(BaseModel):
    query: str | None = None
    price_warm_max: float | None = None
    rooms_min: float | None = None
    rooms_max: float | None = None
    area_sqm_min: float | None = None
    districts: list[str] | None = None
    floor: int | None = None
    listing_type: str | None = None
    has_images: bool | None = None
    near_lat: float | None = None
    near_lon: float | None = None
    radius_km: float = 2.0
    sort_by: str = "relevance"
    limit: int = 10
```

### Modify: `search/models.py`
- Add GeoAlchemy2 `location` column: `mapped_column(Geometry('POINT', srid=4326))`
- Keep raw `latitude`/`longitude` columns for backward compat with seed script
- Add property or hybrid to populate geometry from lat/lon

### Create: `search/service.py`
- `SearchService.__init__(self, db: Session, embedder: Embedder | None = None)`
- The `Embedder` is built once at app startup (in `main.py` lifespan via `build_jina_embedder()` from `core/embedder.py`), stashed on `app.state.embedder`, and injected through FastAPI `Depends(get_embedder)` → `ChatService` → `SearchService`. SearchService doesn't construct it.
- The embedder is wrapped in a `JinaTaskEmbedder(WrapperEmbeddingModel)` that auto-injects Jina v3's task LoRA based on `input_type` — `embed_query()` → `task=retrieval.query`, `embed_documents()` → `task=retrieval.passage`. Callers never touch `extra_body`.
- `async def search(self, filters: SearchFilters) -> pd.DataFrame`
  1. `select(Listing)` → apply WHERE clauses
  2. Districts: `Listing.district.ilike(f"%{d}%")` with OR
  3. Geo: `func.ST_DWithin(Listing.location, point, radius_m)` via GeoAlchemy2
  4. If `filters.query`: embed via `self.embedder.embed()`, ORDER BY cosine distance
  5. Else: order by sort_by
  6. `.limit(filters.limit)`
  7. Convert results to `pd.DataFrame` and return
- The DataFrame includes all user-facing columns + `similarity_score` + `distance_km`

### Verify
- Write `tests/test_search_service.py` with mocked DB + mocked embedder
- Verify filter combinations produce correct SQL

---

## Phase 3: Wire Search into Agent as Tools

### Create: `chat/tools.py`

**`search_apartments`** — the one search tool
- Flat params matching SearchFilters fields
- Constructs `SearchFilters`, calls `search_service.search()`
- Stores DataFrame in `ctx.deps.result_set`
- Returns `ToolReturn`:
  - `return_value`: summary (count, price range, districts, top 5 titles)
  - `metadata`: timing, filter echo

**`get_result_details`** — show specific listings
- `indices: list[int]` — positions in the DataFrame
- Reads from `ctx.deps.result_set.df`
- Returns formatted details (title, price, rooms, area, address, features, URL)

**`get_result_page`** — paginate
- `page: int`, `page_size: int = 5`
- Slices the DataFrame
- Returns formatted page

Note: no separate `refine_results` — the agent just calls `search_apartments` again with narrower filters. The dynamic instruction shows the current result set context, so the agent knows what's active.

### Modify: `chat/agent.py`
- Import and register tools
- Update instructions to guide tool usage

### Verify
- Full conversation flow: search → details → new search with narrower filters
- Test with `TestModel` + mocked SearchService

---

## Phase 4: Phoenix Observability

### Add to `pyproject.toml` dev deps
- `opentelemetry-sdk`, `opentelemetry-exporter-otlp`
- `openinference-instrumentation-pydantic-ai`

### Create: `core/observability.py`
- `setup_observability()`: if enabled, configure OTLP → Phoenix, `Agent.instrument_all()`
- No-op when disabled

### Modify: `core/config.py`
- Add `phoenix_enabled: bool = False`, `phoenix_endpoint: str = "http://localhost:6006"`

### Modify: `main.py`
- Call `setup_observability()` in lifespan

### Verify
- Traces visible at `localhost:6006`

---

## Phase 5: Cleanup & Docs

- Update `CLAUDE.md` tech stack, architecture notes, project structure
- Update `README.md` (layout, endpoints, config)
- Final `.env.example` pass
- Verify `docker compose up` clean

---

## File Matrix

| File | Phase | Action |
|---|---|---|
| `pyproject.toml` | 0+1 | Modify |
| `core/config.py` | 0+1, 4 | Modify |
| `docker-compose.yml` | 0+1 | Modify |
| `.env.example` | 0+1 | Modify |
| `llm/gateway.py` | 0+1 | **Delete** |
| `chat/agent.py` | 0+1, 3 | **Create** |
| `chat/service.py` | 0+1 | Modify |
| `api/chat.py` | 0+1 | Modify |
| `CLAUDE.md` | 0+1 | Modify (stale refs) |
| `search/schemas.py` | 2 | **Create** |
| `search/models.py` | 2 | Modify (add geometry) |
| `search/service.py` | 2 | **Create** |
| `main.py` | 4 | Modify |
| `chat/tools.py` | 3 | **Create** |
| `core/observability.py` | 4 | **Create** |
| `README.md` | 5 | Modify |

## Key Decisions

1. **Pydantic AI `Embedder`** with Jina via OpenAI-compatible endpoint — no raw httpx. Task LoRA (`retrieval.query` / `retrieval.passage`) auto-injected via a `WrapperEmbeddingModel`; embedder built once at app startup and passed through DI.
2. **GeoAlchemy2** for proper spatial column + queries
3. **pandas DataFrame** for result set — easy filtering, slicing, pagination
4. **`instructions=`** not `system_prompt=` — canonical Pydantic AI pattern
5. **`AGUIAdapter`** for FastAPI streaming with thinking indicators
6. **No `/api/search` endpoint** — search is agent-only
7. **One search tool** — re-search replaces refine
8. **Mutable deps** — `ResultSet` on `ChatDeps` persists across tool calls
9. **Built-in retries** — `retries=3` on Agent, `ModelRetry` in tools

## Future (not in this plan)
- `geocode_location` tool (Nominatim / Google Maps)
- `get_travel_time` tool (Transitous API)
- Conversation persistence to Postgres
- User sessions / bookmarks
