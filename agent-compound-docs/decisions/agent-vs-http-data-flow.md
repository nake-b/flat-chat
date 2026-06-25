# Agent stream vs HTTP — the data-flow split

Decided 2026-06-15 during the search-perf refactor.

## Context

Before this refactor every byte of listing data the frontend rendered
flowed through `POST /api/agent` (AG-UI SSE state snapshots). That
worked for a few-listing chat-only UI but doesn't scale to:

- Hundreds of listings with images
- Bookmarks the user wants persisted independently of any conversation
- Shareable URLs (`/listing/<uuid>`) outside the chat context
- Frontend reload preserving the user's last-clicked listing

The agent stream is the right channel for chat-driven interaction; it's
the wrong channel for durable reads.

## Decision

Split into two channels, CQRS-style:

```
                                            ┌── AG-UI SSE (POST /api/agent)
                            ┌── command ─────┤
   Frontend (React)         │                └── chat, tool calls, state deltas
                            │
                            └── query ─────── HTTP REST (GET /api/listings/{id}, ...)
                                              durable reads, cacheable, bookmarks-ready
```

- **AG-UI stream** owns *interpretation* — turning "I want 2-room flats
  in Kreuzberg with U-Bahn" into structured filters, deciding what tools
  to call, and pushing the in-progress result snapshot to the frontend.
- **HTTP REST** owns *durable reads* — fetch this listing by ID, list my
  bookmarks, etc. No LLM involved. Cacheable (`Cache-Control: 5min` on
  listing detail).

Both channels share the same data layer (`listings` ⨝ `listings_geo_context`
⨝ `listings_embeddings`). `ListingService.get_detail(id)` is the single
tier-3 accessor called from both the agent's `open_listing` tool AND the
`GET /api/listings/{id}` route; `ListingService.get_cards(ids)` is the
tier-2 batch accessor behind `GET /api/listings?ids=&view=card`.
`SearchService` stays agent-only — query interpretation is the LLM's job.

## Three tiers of listing data

The split makes the size/weight distinction explicit:

| Tier | What | Size each | Channel |
|---|---|---|---|
| **1 — Marker** | id + lat/lng + price | ~80 bytes | AG-UI state (`result_markers`, every match ≤ MARKER_CAP=5000) |
| **2 — Card** | + chips, area, district, thumbnail URL | ~500 bytes | AG-UI state for the top PREVIEW_N=10 (`preview_cards`); the rest via HTTP `GET /api/listings?ids=&view=card` |
| **3 — Detail** | + full description, image gallery, tier-3 geo-context blob | ~10 KB | HTTP `GET /api/listings/{id}` |

Markers ride the AG-UI state because they ARE the search result set, not
a by-id projection — there is no `view=marker` on the listings
collection. They serialize columnar (`{ids,lats,lngs,prices}`) so even
the 5000-marker cap stays cheap on the wire. Full cards do NOT all ride
the stream: at thousands of matches, tier-2 in state would be hundreds
of KB. Instead the top-10 ship in `preview_cards` and the rest hydrate
on demand.

### Tier-2 batch lazy-hydration — `GET /api/listings?ids=&view=card`

The collection route takes a repeated `ids` query param and an AIP-157
`view` enum (`card` / `detail`) and returns `ListingCard[]` in request
order. Constraints: ≤100 ids per call, cacheable. The frontend's card
strip windows the marker list and fires this for the ids that scroll
into view, caching results in a client-side `cardCache`. This is the
tier-2 lazy-hydration channel; the single `GET /api/listings/{id}` still
serves tier-3 detail, unchanged.

## Active-listing detail in state

When a listing is open (one at a time), its tier-3 detail lives in
`SessionState.active_listing_detail` so the LLM has full context for
follow-up questions ("is the area safe?") without redundant tool calls.
~10 KB per active selection; negligible.

Two trigger paths populate it:
- **Frontend click** — `setState({active_id})` + `GET /api/listings/{id}`
  + `setState({active_listing_detail})`. Primary path; 1 DB hit.
- **Agent `open_listing(indices=[k])`** — tool calls
  `ListingService.get_detail(id)` internally + pushes both fields via
  state delta. 1 DB hit.

Either way, when `active_id` is set, `active_listing_detail` is
populated. The agent's context module emits "do NOT call open_listing
for #N" alongside the data so the LLM never re-fetches what it already
has.

## New-programmer walkthrough

Imagine you just joined the team. Here's how a search-and-detail flow
works:

The cast:
- Browser (React) → SSE + HTTP
- FastAPI backend → agent + search + listings services
- Postgres → silver/gold/platinum tables

**1. User opens the app.** Browser posts `/api/conversations` to get a
`conversation_id`. Connects to `/api/agent` SSE.

**2. User types** *"2-room flats in Kreuzberg with U-Bahn under 1500€"*.
Message goes over AG-UI.

**3. Agent calls `search_apartments` tool.** `SearchService` runs one
SQL query against `listings ⨝ listings_geo_context` (gold). All filters
are B-tree index lookups. Returns `(markers, preview_cards, total)`.

**4. Tool updates SessionState.** `search_params`, `total_results`,
`result_markers`, `preview_cards` set; `active_id` cleared.
`STATE_SNAPSHOT` event emitted on the SSE stream (markers serialized
columnar).

**5. Browser renders** map markers (clustered) from
`decodeMarkers(state.result_markers)` + the card strip, first-painting
the top-10 from `state.preview_cards`.

**6. User scrolls the strip past the preview.** Frontend windows the
marker list, batches the newly-visible ids into
`GET /api/listings?ids=&view=card`, and caches the returned cards in
`cardCache`. Hovering an already-hydrated card is a pure cache read — no
network call.

**7. User clicks card #3.** Frontend updates `active_id` locally, fires
`GET /api/listings/3`. `ListingService.get_detail` returns tier-3 (~5ms
PK lookup). Detail panel renders from HTTP response. Frontend writes the
detail back to `state.active_listing_detail` so the agent has it on the
next turn. Browser caches the HTTP response for 5 min.

**8. User asks** *"of these, which are the quietest?"* — agent answers
from the `preview_cards` already in memory (their `noise_label`). Zero
DB hits for the head of the list.

**9. User asks** *"show me only the quietest ones"* — agent calls
`search_apartments` again with `max_noise: "quiet"` added. Steps 3–5
repeat with new snapshot.

## Refinement caching — not implemented, see `session-state-design.md`

Today: every refinement re-runs SQL against gold (cheap enough). Future:
if the in-memory snapshot becomes a basis for client-side filter UI,
integrate pandas/polars into `SessionState` for refinement without a
roundtrip. See the TODO in `CLAUDE.md` and the rationale in
`session-state-design.md`.

## Sources

- [AG-UI State Management — official docs](https://docs.ag-ui.com/concepts/state)
- [CopilotKit AG-UI documentation](https://docs.copilotkit.ai/agentic-protocols/ag-ui)
- [CQRS Pattern (Azure Architecture Center)](https://learn.microsoft.com/en-us/azure/architecture/patterns/cqrs)
- [CQRS for AI Agents (Tacnode)](https://tacnode.io/post/cqrs-pattern)
- [Master the 17 AG-UI Event Types (CopilotKit)](https://www.copilotkit.ai/blog/master-the-17-ag-ui-event-types-for-building-agents-the-right-way)
