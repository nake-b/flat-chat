# services/frontend/CLAUDE.md

Frontend-specific context for Claude Code. The root CLAUDE.md has
project-wide tech stack + conventions; this file has what's specific to
the React SPA.

## Layout

```
src/
  main.tsx                → Bootstraps session, mounts <CopilotKit> with HttpAgent → /api/agent
  App.tsx                 → Chat-host layout: chat left ~40%, map+cards right (Option-X resize)
  state/
    SessionState.ts       → CANONICAL TypeScript mirror of backend Pydantic SessionState
    UiState.ts            → Compat re-export of SessionState (existing imports keep working)
    toolStatus.ts         → Tool-name → status-pill label registry
    cardCache.ts          → zustand store of lazily-hydrated tier-2 ListingCards (by id)
  hooks/
    useSessionState.ts    → CANONICAL. useCoAgent<SessionState> + activate() helper
    useUiState.ts         → Compat re-export of useSessionState
    useHover.ts           → zustand store for client-local hover state
  api/
    session.ts            → POST /api/conversations to allocate a thread_id
  components/
    ChatPane.tsx, MapPane.tsx, CardsPane.tsx, CardStrip.tsx, CardDetail.tsx
```

## The data-flow split (frontend perspective)

Two channels to the backend:

1. **AG-UI SSE** (via CopilotKit's `useCoAgent`) — `useSessionState()`
   gives us the live `SessionState` (mirrors the backend's per-conversation
   in-memory state). Carries tier-1 markers (every match, columnar) +
   the top-10 `preview_cards`. The remaining cards do NOT ride this
   channel — they hydrate on demand (channel 2).

2. **HTTP REST** (via `fetch`) —
   - `GET /api/listings/{id}` returns tier-3 detail (full description +
     image gallery + geo-context blob). Fired by
     `useSessionState().activate(id)` on card click.
   - `GET /api/listings?ids=&view=card` returns tier-2 `ListingCard[]`
     in request order — the lazy-hydration channel for cards past the
     preview window, cached in the `cardCache` zustand store.

The `activate(id)` helper does the orchestration:
- Sets `state.active_id` immediately for instant card highlight
- Fires `GET /api/listings/{id}`
- On response, writes `state.active_listing_detail` so the backend
  (agent) has the same data on the next turn

Decision docs:
- [`agent-vs-http-data-flow.md`](../../agent-compound-docs/decisions/agent-vs-http-data-flow.md)
- [`session-state-design.md`](../../agent-compound-docs/decisions/session-state-design.md)

## Backend sync — `SessionState.ts`

Manual TypeScript mirror of `services/backend/src/flat_chat/chat/
session_state.py`. Keep these two files in sync — fields and optionality
must match exactly. The per-listing detail shapes mirror
`services/backend/src/flat_chat/listings/context.py`.

Note: `result_markers` mirrors the SERIALIZED COLUMNAR shape
(`{ids,lats,lngs,prices}`) that crosses the wire, not the backend's
in-memory `list[Marker]`. Decode it with `decodeMarkers(...)` before
use.

Label literal vocab (`NoiseLabel`, `MssStatus`, etc.) traces to
[`geo-context-thresholds.md`](../../agent-compound-docs/decisions/geo-context-thresholds.md).

No automation — top-of-file comment is the contract. If drift becomes
costly, add `pydantic-to-typescript` or a small in-repo codegen step.

## Status pills (tool-call lifecycle)

Status-pill copy ("Searching Kreuzberg…", "Found 12 listings…") is NOT
in `SessionState`. The frontend derives lifecycle labels directly from
AG-UI tool-call events via the tool-name → label registry in
`state/toolStatus.ts`, consumed by `useCopilotAction` per backend tool.
The "Thinking" phase is rendered via `useCoAgentStateRender` and
suppresses itself while any tool pill is executing.

Adding a new backend tool: register a label in `toolStatus.ts` and a
`useCopilotAction` handler. Zero backend churn — tools stay pure data
mutators.

## Card-strip / Map / Detail rendering

- **Map markers** (`MapPane.tsx`) — clustered, plots ALL markers via
  `decodeMarkers(state.result_markers)`. MapLibre + supercluster.
  Click → `activate(id)`.
- **Card strip** (`CardStrip.tsx`) — horizontal scrolling row that
  windows the full marker list. First-paints from `state.preview_cards`
  (top-10), then lazy-hydrates the rest as they scroll into view via
  `GET /api/listings?ids=&view=card` into the `cardCache` zustand store.
  Click → `activate(id)`. Hydration lifecycle: a new result set (detected
  via a cheap `length:firstId:lastId` signature) clears `cardCache`, resets
  the scroll window to the top, and re-seeds from `preview_cards`; the debounce
  effect is NOT subscribed to the cache (it reads `useCardCache.getState()`)
  so a cache write can't cancel an in-flight fetch; and ids the backend has no
  listing for are recorded in a `notFound` tombstone ref so a deleted/expired
  listing isn't re-requested every window pass.
- **Card detail** (`CardDetail.tsx`) — when `state.active_id` is set,
  swaps in. Reads tier-3 detail from `state.active_listing_detail` (its
  `apt` tier-2 prop is now optional; the active card is resolved from
  the card cache ∪ preview by `CardsPane`, falling back to
  `active_listing_detail`). Renders image gallery + amenity chips + full
  stat grid + geo-context block (transit, schools, parks, playground,
  hospitals, water, noise with sub-numerics, greenery + m², density +
  persons/hectare, MSS + social_inequality, disabled parking).

## Performance — windowing up to MARKER_CAP=5000

The strip windows the marker list (manual scroll-window) up to
`MARKER_CAP`=5000 markers, hydrating only the visible slice via the
batch card endpoint; the top-10 `preview_cards` cover the first paint.
The map clusters all markers. If the manual window stutters in practice,
swap to react-window / react-virtuoso; semantic-search HNSW handles
thousands trivially.

## Running

```bash
docker compose up frontend nginx    # served at http://localhost
cd services/frontend && pnpm dev    # for local dev (Vite proxy → backend)
```

## Compat aliases

For the search-perf refactor, `UiState` (old name) is preserved as a
re-export of `SessionState` (new name). Same for `useUiState` →
`useSessionState`. Migrate components at leisure; both work today.
`active_listing_context` is aliased to `ListingDetail` for the same
reason.
