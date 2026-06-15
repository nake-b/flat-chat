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
⨝ `listings_embeddings`). `ListingService.get(id)` is the single accessor
called from both the agent's `open_listing` tool AND the HTTP route.
`SearchService` stays agent-only — query interpretation is the LLM's job.

## Three tiers of listing data

The split makes the size/weight distinction explicit:

| Tier | What | Size each | Channel |
|---|---|---|---|
| **1 — Marker** | id + lat/lng + price | ~80 bytes | AG-UI state |
| **2 — Card** | + chips, area, district, thumbnail URL | ~500 bytes | AG-UI state |
| **3 — Detail** | + full description, image gallery, tier-3 geo-context blob | ~10 KB | HTTP `GET /api/listings/{id}` |

At 500 listings, tier-2 = ~250 KB of state — fine for SSE. Tier-3 at
the same scale would be 5 MB — too much. The HTTP channel only delivers
tier-3 for one listing at a time (the one being viewed).

## Active-listing detail in state

When a listing is open (one at a time), its tier-3 detail lives in
`SessionState.active_listing_detail` so the LLM has full context for
follow-up questions ("is the area safe?") without redundant tool calls.
~10 KB per active selection; negligible.

Two trigger paths populate it:
- **Frontend click** — `setState({active_id})` + `GET /api/listings/{id}`
  + `setState({active_listing_detail})`. Primary path; 1 DB hit.
- **Agent `open_listing(indices=[k])`** — tool calls `ListingService.get(id)`
  internally + pushes both fields via state delta. 1 DB hit.

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
are B-tree index lookups. Returns ordered `list[UiApartment]`.

**4. Tool updates SessionState.** `search_params`, `total_results`,
`results` set; `active_id` cleared. `STATE_SNAPSHOT` event emitted on
the SSE stream.

**5. Browser renders** map markers (clustered) + virtualised card list
from `state.results`.

**6. User hovers a card.** Pure frontend — reads `state.results[index]`.
No network call.

**7. User clicks card #3.** Frontend updates `active_id` locally, fires
`GET /api/listings/3`. `ListingService.get` returns tier-3 (~5ms PK
lookup). Detail panel renders from HTTP response. Frontend writes the
detail back to `state.active_listing_detail` so the agent has it on the
next turn. Browser caches the HTTP response for 5 min.

**8. User asks** *"of these, which are the quietest?"* — agent reads
`state.results` (already in memory) and answers from the snapshot's
`noise_label`. Zero DB hits.

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
