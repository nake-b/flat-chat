# flat-chat — Hackathon Starter

Welcome! This repo is a **fully working Berlin apartment app with the agent
removed**. Everything around the agent is built and running:

- a Postgres database with ~1,500 real Berlin listings enriched with geo-context
  (transit, parks, schools, noise, density, socioeconomic status, …),
- a `SearchService` that turns structured filters into fast SQL,
- a `ListingService` for full per-listing detail,
- a React frontend (chat + map + cards) that renders whatever your agent produces.

**Your job:** plug your own agent framework into one seam and make the search
intelligent. Pick anything — Pydantic AI, LangGraph, CrewAI, the Anthropic or
OpenAI SDK directly, a hand-rolled state machine. Nothing here forces a choice.

---

## 1. Get it running (5 minutes)

```bash
# 1. clone the starter
git clone --branch hackathon-starter git@github.com:nake-b/flat-chat.git my-team
cd my-team

# 2. config — the placeholder agent needs NO keys
cp .env.example .env

# 3. load the shared database (ask the organizers for flat-chat-db-snapshot.tgz,
#    drop it in the repo root, then:)
./scripts/restore-db-snapshot.sh

# 4. boot everything
docker compose up --build
```

Open <http://localhost>, type *"flats in Kreuzberg"*, and you'll see listings
drop onto the map. That's the **placeholder agent** (`ExampleSearchBackend`) —
a dumb keyword matcher with no LLM. You're going to replace it.

> Working on a team in one shared GitHub repo? Fork it on GitHub, or push to a
> namespaced branch (`team-x/...`) so you don't collide with other teams.

---

## 2. The one seam you implement

The whole agent contract is one async method, `AgentBackend.run`, defined in
[`services/backend/src/flat_chat/chat/backend.py`](services/backend/src/flat_chat/chat/backend.py):

```python
class AgentBackend(Protocol):
    def run(
        self, *, run_input: RunAgentInput, deps: ChatDeps
    ) -> AsyncIterator[BaseEvent]: ...
```

The HTTP/SSE plumbing (`chat/service.py`, `api/agent.py`) is done for you. It
parses the incoming AG-UI request, hands you `run_input` (the conversation) and
`deps` (your service handles + the shared state), brackets your run with
`RUN_STARTED` / `RUN_FINISHED`, SSE-encodes whatever you `yield`, and persists
the session afterward. **You don't touch any of that.**

### What you get — `deps: ChatDeps`

| Field | Use |
|---|---|
| `deps.search_service.search(params)` | `→ (list[UiApartment], total)`. The core search. Build a `SearchParams` (see §4) and run it. |
| `deps.listing_service.get(id)` | `→ ListingDetail \| None`. Full detail for one listing. |
| `deps.state` | The shared `SessionState` (see §3). **Mutate this** and emit a snapshot to drive the UI. |
| `deps.session` | The conversation (history, id). Usually you can ignore it. |

### What you emit — `ag_ui` events

`run` is an async generator of [AG-UI protocol](https://docs.ag-ui.com) events.
You do **not** emit `RUN_STARTED` / `RUN_FINISHED` — `ChatService` does. A normal
turn yields, in any order:

- a **text reply** → `TextMessageStart` → `TextMessageContent` → `TextMessageEnd`
  (renders the chat bubble),
- optionally a **tool-call lifecycle** → `ToolCallStart` → `ToolCallArgs` →
  `ToolCallEnd` → `ToolCallResult` (lights up a status pill — see §5),
- a **state snapshot** → `StateSnapshotEvent(snapshot=deps.state.model_dump(mode="json"))`
  (re-renders the map + cards).

`chat/backend.py` ships small helpers — `text_message(...)`, `tool_call(...)`,
`state_snapshot(deps)`, `latest_user_text(run_input)` — so you rarely build
events by hand. If your framework has its own AG-UI adapter, you can yield its
events straight through instead.

### Wiring your backend in

Implement your class, then point the factory at it in
[`core/dependencies.py`](services/backend/src/flat_chat/core/dependencies.py):

```python
# return ChatService(..., backend=ExampleSearchBackend())
return ChatService(..., backend=MyAwesomeAgent())
```

That single line is the swap. Read
[`chat/example_backend.py`](services/backend/src/flat_chat/chat/example_backend.py)
end-to-end first — it's the whole pattern in ~40 lines.

---

## 3. The state contract — `SessionState`

The frontend renders **entirely** from `deps.state`
([`chat/session_state.py`](services/backend/src/flat_chat/chat/session_state.py),
mirrored in `services/frontend/src/state/SessionState.ts`). Emit a
`StateSnapshotEvent` after you change it and the UI updates. Three tiers:

| Field | Tier | Drives |
|---|---|---|
| `search_params` | 1 | chat context (the filters you applied) |
| `total_results` | 1 | "showing N of M" |
| `results: list[UiApartment]` | 2 | **map markers + card strip** (lat/lng, price, rooms, district, chips, thumbnail) |
| `active_id` | 3 | which card is expanded |
| `active_listing_detail: ListingDetail` | 3 | the detail panel (full description, images, geo-context) |

`results` come straight from `search_service.search(...)` — assign and snapshot.
`active_listing_detail` the frontend usually fetches itself over HTTP
(`GET /api/listings/{id}`) on card click, then writes back into state, so your
agent sees it on the next turn. You can also set it yourself from
`listing_service.get(id)` when the user says "tell me about #3".

---

## 4. The search grammar — `SearchParams`

Everything `search_service.search` understands is in
[`search/schemas.py`](services/backend/src/flat_chat/search/schemas.py). It's a
wide, flat, all-optional Pydantic model — every field defaults to "don't filter".
Highlights:

- **money**: `price_warm_min/max`, `price_cold_max`
- **size**: `rooms_min/max`, `bedrooms_min`, `area_sqm_min/max`
- **location**: `districts: list[str]` (ILIKE substring), `near_lat/near_lon` + `radius_km`
- **amenities** (tri-state `True`/`False`/`None`): `wbs_required`, `is_furnished`,
  `has_balcony`, `has_kitchen`, `has_elevator`, `has_images`
- **geo-context**: `transit`, `school`, `hospital`, `mss` (bundled filters in
  [`search/geo_filters.py`](services/backend/src/flat_chat/search/geo_filters.py)),
  plus `near_park`, `near_playground`, `near_water`, `max_noise`, `min_greenery`,
  `density`
- **ranking**: `sort_by ∈ {relevance, price, area, recent}`, `limit` (≤ 500)

The label vocabulary (`NoiseLabel`, `DensityLabel`, `GreeneryLabel`, `MssStatus`,
`NearSpec`, …) lives in
[`listings/types.py`](services/backend/src/flat_chat/listings/types.py) and the
numeric thresholds behind those labels in
[`listings/thresholds.py`](services/backend/src/flat_chat/listings/thresholds.py).
Translating "a quiet, green place near a U-Bahn under €1200" into these fields is
exactly the agent intelligence you're building.

> `sort_by="relevance"` uses semantic vector ranking and needs `JINA_API_KEY`
> set. Without it, relevance degrades gracefully to recency — search still works.

---

## 5. Status pills (optional polish)

The chat shows a status pill per tool call. The label comes from a name→label
registry in `services/frontend/src/state/toolStatus.ts`. The starter keeps the
`search_apartments` entry, which the placeholder emits. Emit a `tool_call` with
`name="search_apartments"` and you get the pill for free; add your own tool names
to that file (one entry each) for custom labels. Pure cosmetics — the map and
cards render from the state snapshot regardless.

---

## 6. The database snapshot

The app is useless without data, and you must **not** run the scrapers. Instead,
the organizers share a raw Postgres volume snapshot (`flat-chat-db-snapshot.tgz`,
~600 MB) holding ~1,500 enriched listings + all the geo-context tables.

```bash
./scripts/restore-db-snapshot.sh    # wipes local DB, restores the snapshot
```

It's a bit-for-bit copy of the `postgres_data` volume — safe because everyone
runs the identical `services/postgres/Dockerfile` image (pgvector + PostGIS).
To re-share an updated DB, `./scripts/make-db-snapshot.sh` produces a fresh tgz.

---

## 7. What was removed vs. kept

**Removed** (the old Pydantic-AI agent): `chat/agent.py`, `chat/tools.py`,
`chat/llm_context.py`, `chat/providers/`, and all LLM-provider config/keys.

**Kept / added**: `search/`, `listings/`, the whole frontend, sessions, the
`/api/listings/{id}` + `/api/conversations` routes, and the new
`chat/backend.py` seam + `chat/example_backend.py` placeholder.

A complete reference implementation (the original app, agent intact) is tagged
`hackathon-reference-v1` if you want to see one worked example:
`git clone --branch hackathon-reference-v1 git@github.com:nake-b/flat-chat.git`.

Happy hacking! 🏠
