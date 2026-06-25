# services/backend/CLAUDE.md

Backend-specific context for Claude Code. The root CLAUDE.md has
project-wide tech stack + conventions; this file has what's specific to
the backend Python package.

## Layout

```
src/flat_chat/
  main.py              → FastAPI app + lifespan + router registration
  core/                → DB engines (sync + async), config, observability, deps
  api/                 → HTTP routes — thin
                          chat.py     POST /api/conversations + GET history
                          agent.py    POST /api/agent (AG-UI SSE)
                          listings.py GET /api/listings/{id} (detail)
                                      + GET /api/listings?ids=&view=card (batch tier-2)
  chat/                → Agent orchestration domain
                          agent.py        Agent(toolsets=[toolset], instructions=...)
                          tools.py        FunctionToolset[ChatDeps]
                          llm_context.py  LlmResultSetView + build_dynamic_state_prompt
                          session_state.py SessionState (renamed from ui_state.py)
                          state.py        ChatSession + ChatDeps
                          sessions.py     SessionStore Protocol + InMemorySessionStore
                          service.py      ChatService — dispatches AG-UI run
                          providers/      Provider dispatch (Anthropic / Azure)
  search/              → Query execution domain
                          service.py      SearchService — async, returns (markers, preview_cards, total)
                          schemas.py      SearchParams + SortBy
                          geo_filters.py  Filter input shapes only
  listings/            → NEW. Shared listing-domain primitives.
                          models.py       Listing + ListingGeoContext + ListingEmbedding ORMs
                          types.py        Literal types (NoiseLabel, MssStatus, ...)
                          context.py      ListingDetail + ListingCard + nested dataclasses
                          projection.py   Shared tier-2 ListingCard projection (preview + get_cards)
                          labels.py       bucket_*, walk_minutes, encode_modes, ...
                          thresholds.py   Single source of truth for numeric constants
                          service.py      ListingService — async get_detail(id) / get_cards(ids)
```

## Layering rules

Strict dependency direction, top to bottom:

```
api/      → chat/, listings/, core/
chat/     → search/, listings/, core/
search/   → listings/, core/
listings/ → core/
core/     → (no domain deps)
```

`api/` never imports from `search/`. `listings/` is a leaf — it doesn't
import from `chat/`, `search/`, or `api/`.

## Database — two engines

- **`sync_engine`** (`postgresql+psycopg2`): Alembic + any sync context.
- **`async_engine`** (`postgresql+asyncpg`): every FastAPI request path.

Both wire the per-request SQL comment tagging (`/* session=… run=… */`)
and DB-error logging through `core/observability.py` contextvars. The
async engine attaches hooks to `async_engine.sync_engine` under the
hood (SQLAlchemy wraps sync DBAPI for async).

Decision doc: [`async-database-layer.md`](../../agent-compound-docs/decisions/async-database-layer.md).

## Data flow

Two channels between frontend and backend:

- **AG-UI SSE (`POST /api/agent`)** — chat + tool calls + state deltas.
  Carries tier-1 markers (EVERY match, ≤ `MARKER_CAP`=5000) + the top
  `PREVIEW_N`=10 tier-2 cards via `SessionState`. Markers serialize
  columnar on the wire so thousands stay cheap. Heavy data (tier-3,
  images) and the remaining cards do NOT go here.
- **HTTP REST** — direct listing reads.
  - `GET /api/listings/{id}` → tier-3 detail + image gallery URLs.
    `Cache-Control: 5min`. Backed by `ListingService.get_detail(id)`,
    which also powers the agent's `open_listing` tool.
  - `GET /api/listings?ids=&view=card` → batch tier-2 hydration in
    request order (≤100 ids, cacheable). Backed by
    `ListingService.get_cards(ids)`. This is the lazy-hydration channel
    for cards beyond the preview window.

`SearchService` is agent-only (`chat/tools.py` is the sole caller — no
HTTP route exposes it). `ListingService` is shared.

Decision doc: [`agent-vs-http-data-flow.md`](../../agent-compound-docs/decisions/agent-vs-http-data-flow.md).

## SessionState — the in-memory snapshot

`chat/session_state.py:SessionState` is the canonical representation of
the active conversation. One object, three readers:

1. The LLM (via `build_dynamic_state_prompt` — emits `<current_state>` +
   `<user_focus>` XML)
2. The frontend (renders markers/cards/detail from these fields)
3. The pagination tool (zero-DB-hit re-read)

Co-locates the applied search (`search_params`) with the result set,
now split by tier:

- `result_markers` — EVERY match as thin tier-1 markers
  (`{id,lat,lng,price_warm}`, ≤ `MARKER_CAP`=5000). The map source AND
  the ordered result set. Serialized COLUMNAR on the wire
  (`{ids,lats,lngs,prices}`) via a `@field_serializer`, decoded back by
  a paired `@field_validator` (symmetric — the AG-UI envelope echoes
  state back and `_extract_incoming_state` re-validates it).
- `preview_cards` — the top `PREVIEW_N`=10 full `ListingCard`s, hot for
  the LLM and the card strip's first paint.
- `total_results` — a real count (`len(result_markers)`, or `COUNT(*)`
  when the 5000 cap binds).

The rest of the cards hydrate on demand by id (see the batch route
above). No more separate DataFrame + UiState split.

Decision doc: [`session-state-design.md`](../../agent-compound-docs/decisions/session-state-design.md).

## Search query — B-tree on gold

`SearchService.search()` joins `listings ⨝ listings_geo_context (⨝
listings_embeddings)` and returns `(markers, preview_cards, total)` —
all matching markers (hard-capped server-side at `MARKER_CAP`=5000), the
top `PREVIEW_N`=10 tier-2 cards, and the count. There is no per-search
`limit` arg anymore. The shared tier-2 projection lives in
`listings/projection.py` and is reused by both this preview and
`ListingService.get_cards(ids)`. All geo-context filters are B-tree
predicates on gold's denormalised columns — no LATERAL joins, no
EXISTS-with-ST_DWithin, no per-row spatial work. The only spatial
predicate that survives is `ST_DWithin` on `listings.location` for
explicit `near_lat/near_lon` proximity search; it hits the functional
GiST index.

The 12-query `open_listing` fan-out is gone — replaced by one PK lookup
through `ListingService.get_detail(id)` + 6 small top-N reads from the
junction tables.

**POI filters** (transit / schools / hospitals / parks / playgrounds /
water) use EXISTS-against the matching `listings_nearby_*` junction
table. Attribute filters (transit modes/lines/stop_name, school_type,
hospital tier) work end-to-end. **Scalar/field filters** (mss /
max_noise / min_greenery / density) read chip columns on
`listings_geo_context`. `max_noise` is optimistic-include via
`or_(IS NULL, < cutoff)` — paired with the 50 m coverage gate inside
`enrich_noise`. See
[`spatial-neighbor-tables.md`](../../agent-compound-docs/decisions/spatial-neighbor-tables.md).

`GET /api/health?extended=true` reports `gold_orphans` for drift detection.

Decision docs: [`gold-platinum-layers.md`](../../agent-compound-docs/decisions/gold-platinum-layers.md), [`spatial-neighbor-tables.md`](../../agent-compound-docs/decisions/spatial-neighbor-tables.md).

## LLM prompt assembly

Pydantic AI composes the agent's system prompt as:

1. **Agent `instructions=`** (cached, static): role / UI / honesty /
   neutrality. In `chat/agent.py`.
2. **`@toolset.instructions`** (cached, static): tool protocol + phrase
   map. In `chat/tools.py`.
3. **`@agent.instructions`** (uncached, per-turn): `<current_state>` +
   `<user_focus>` from `build_dynamic_state_prompt`. In `chat/llm_context.py`.

State-dependent rules ("don't reopen the active listing") MUST go in
the dynamic layer or they'd break the prompt cache. ~5600 cached prefix
tokens per turn verified.

Decision doc: [`dynamic-prompt-instructions.md`](../../agent-compound-docs/decisions/dynamic-prompt-instructions.md).

## Bucket labels & thresholds

`listings/labels.py` + `listings/thresholds.py` are the single source of
truth for numeric → categorical mappings. Search reads them for filter
parsing (`max_noise="quiet"` → `noise_total_lden < 55`); chat reads
them for result-time label application (`noise_total_lden=58` → `"lively"`).

Both directions share the same numbers. A threshold tweak is one place
to edit; no gold rebuild needed.

Each constant traces to a row in
[`geo-context-thresholds.md`](../../agent-compound-docs/decisions/geo-context-thresholds.md).

## Running

```bash
docker compose up backend                      # Backend at http://localhost (via nginx)
cd services/backend && alembic upgrade head    # Apply migrations
alembic downgrade -1 && alembic upgrade head   # Round-trip test
```

## Debugging

Phoenix at `http://localhost:6006`. SQL is tagged with `/* session=…
run=… */` for every statement fired during a request; map a stuck row
in `pg_stat_activity` back to the conversation via `just psql-active` /
`just psql-session <id>`.

ContextVars (`session_id_var`, `run_id_var` in `core/observability.py`)
are set in `ChatService.dispatch_agent_request`; the SQL hook reads them.

## Tests

Two tiers under `tests/`:

- **Pure unit** (`test_health.py`, `test_observability.py`) — no DB, run
  with bare `pytest`.
- **Integration** (`test_alembic_round_trip.py`, `test_search_service.py`)
  — execute against Postgres. Gated on `TEST_DATABASE_URL`; skipped
  silently when unset. Setup + conventions in
  [`tests/README.md`](tests/README.md).

`test_search_service.py` is the regression suite for the search SQL —
one test per geo-context filter, each actually executes against
Postgres. This is the layer that catches operator-shape bugs that
compile cleanly in SQLAlchemy but Postgres rejects at runtime (the
June 2026 `jsonb ?| jsonb` and `text[] && varchar[]` incidents).

When adding a new search filter, add a test in the same change.

## TODOs

- Auth / user identity not implemented; `users/` stub kept for future.
- Bookmarks not implemented; slot ready (`listings/bookmarks_service.py`
  + `api/bookmarks.py` following the same pattern as listings).
- Refinement cache deferred (see `session-state-design.md` — if
  refinement becomes slow, integrate pandas into `SessionState` and add
  `state.refine(params)` for in-memory filtering).
