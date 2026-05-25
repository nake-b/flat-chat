# Frontend Stack & Generative UI Integration

Decided 2026-05-21.

## Context

The current frontend is a minimal `Chat.tsx` (~89 lines) that does request/response POSTs to `/api/conversations/{id}/messages`. No streaming, no map, no apartment cards, no state management. The backend has a polished Pydantic AI agent with three tools (`search_apartments`, `get_result_page`, `get_result_details`) and a `ResultSet` formatting layer, but the UI doesn't surface tool-call lifecycle, structured results, or apartment geo data.

The product thesis is **conversational search** — the user describes an apartment and the agent finds and explains matches. To preserve that thesis the frontend has to make the chat thread the primary surface and turn the map + apartment cards into *artifacts the agent updates*, not the primary filter UI.

Pydantic AI now ships first-party support for the **AG-UI Protocol** (`pydantic_ai.ui.ag_ui.AGUIAdapter`), an open event-streaming protocol from the CopilotKit team. AG-UI is the seam that lets the agent stream not just text deltas but also tool-call lifecycle events and a typed shared state object — exactly what we need to drive sibling UI components (chat, map, cards) from a single source of truth.

Research, alternatives considered, and the long-form discussion that led to these choices are archived in `agent-compound-docs/conversations/frontend-stack-and-generative-ui.md`. This document captures only the locked-in decisions.

## Decision: AG-UI + CopilotKit + MapLibre, chat-host layout

### Chat protocol: AG-UI via `pydantic-ai-slim[ag-ui]`

Switch the backend dep from `pydantic-ai` to `pydantic-ai-slim[ag-ui]`. Add a new `POST /api/agent` route that calls `AGUIAdapter.dispatch_request(request, agent=agent, deps=...)`. The adapter owns SSE framing, JSON-Patch state deltas, and tool-call event emission.

Keep `POST /api/conversations` (session creation) and `GET /api/conversations/{id}/messages` (history reload). Remove `POST /api/conversations/{id}/messages` — it's replaced by the AG-UI streaming route.

### Chat UI library: CopilotKit

`@copilotkit/react-core` + `@copilotkit/react-ui`. The killer primitive is `useCoAgent<UiState>({ name, initialState })` — a hook that mirrors backend agent state on the frontend and re-renders any subscribed component when the agent mutates `ctx.deps.ui_state`. Sibling React components (chat, map, cards) all subscribe via this hook to slices of the same state.

Tool-call lifecycle pills ("Searching Kreuzberg…", "Found 12 listings…", "Thinking…") are rendered inline in the chat thread via `useCopilotAction({ name, render })` per backend tool (executing/complete phases) plus a single `useCoAgentStateRender` for the Thinking phase. UI copy lives entirely on the frontend in a tool-name → label registry. See §Status-pill lifecycle below.

### Map: MapLibre GL JS v5 + `@vis.gl/react-maplibre` + self-hosted Protomaps

- **Engine**: `maplibre-gl@^5` — OSS, WebGL2, vector tiles, built-in GeoJSON clustering + heatmap layers.
- **React wrapper**: `@vis.gl/react-maplibre@^8` — the dedicated MapLibre wrapper from vis.gl (split out of `react-map-gl`). Cleaner deps and TS types for a MapLibre-only project than the multi-target `react-map-gl/maplibre`.
- **Tiles**: self-hosted Protomaps `.pmtiles` Berlin extract served from nginx at `/tiles/berlin.pmtiles`. No vendor lock-in, no per-load fees, tens-of-MB file. Client reads via the `pmtiles` library and the `pmtiles://` style URL.
- **Clustering**: MapLibre's native `cluster: true` GeoJSON source. `setFeatureState({source, id}, {hover: true})` drives bidirectional highlight with the card list.

### Layout: chat-host with persistent map + cards artifact

```
┌─────────────────────┬─────────────────────────────────┐
│                     │             MAP   (≈70%)        │
│      CHAT (~40%)    │   (always visible)              │
│                     ├─────────────────────────────────┤
│                     │  CARDS (thin strip, ≈30%)       │
└─────────────────────┴─────────────────────────────────┘

When a card is clicked (Option X — cards grow, map shrinks):

┌─────────────────────┬─────────────────────────────────┐
│      CHAT           │  MAP   (≈50%)                   │
│                     ├─────────────────────────────────┤
│                     │  CARD DETAIL (≈50%, sibling     │
│                     │  cards hide, "back to results") │
└─────────────────────┴─────────────────────────────────┘
```

- Map keeps full real estate even in detail view — apartment context stays spatially anchored.
- Cards-as-horizontal-strip (Netflix-row) — fast left/right scan.
- In-place expansion preserves the user's mental model better than a drawer/modal.
- Animated flex resize on `active_id` flip (30→50% cards, 70→50% map).

### State ownership: backend-authoritative `UiState`, frontend mirror

```
Backend (truth)                       Frontend (mirror)
StateDeps[UiState]   ───── SSE ───►   CopilotKit store
agent tools mutate   JSON Patch       useCoAgent() reads
ctx.deps.ui_state    deltas           components subscribe
```

A new `chat/ui_state.py` defines:

```python
class UiApartment(BaseModel):
    id: str
    lat: float | None
    lng: float | None
    price_warm_eur: float | None
    rooms: float | None
    area_sqm: float | None
    district: str | None
    title: str | None
    address: str | None
    source_url: str | None
    image_url: str | None

class UiState(BaseModel):
    results: list[UiApartment] = []
    active_id: str | None = None     # which card is expanded
```

Existing tools mutate **both** `ResultSet` (LLM-facing prose, untouched) and `UiState` (UI-facing structured data). The two are parallel projections of the same `SearchService.search()` DataFrame — *not* a replacement for `ResultSet`. The LLM never sees `UiState`; the UI never sees `ResultSet`. See `llm-tool-result-design.md` for `ResultSet`'s contract.

Write-back path: when the user clicks a card, the frontend calls `setState({ active_id })`; CopilotKit streams the update back to the backend so the agent sees what the user is looking at on the next turn.

### Status-pill lifecycle: frontend-owned, AG-UI tool-call driven

The "Thinking…" / "Searching Kreuzberg…" / "Found 12 in Kreuzberg" pills in the chat thread are driven by AG-UI's native tool-call lifecycle events, not by a shared-state field. Backend tools mutate data only; the frontend owns all status copy in one file.

**Wildcard subscription.** Each backend tool emits the AG-UI sequence `TOOL_CALL_START` → `TOOL_CALL_ARGS` → `TOOL_CALL_END` → `TOOL_CALL_RESULT`. We subscribe to **all** of them through a single CopilotKit registration: `useCopilotAction({ name: "*", render: ({ name, status, args, result }) => … })`. CopilotKit's action validator treats `name: "*"` as a render-only catch-all (no `parameters` required) and never injects it into the LLM's tool list, so it stays out of the AG-UI envelope and doesn't trip Pydantic AI's `RunAgentInput` validation.

CopilotKit exposes the lifecycle as `inProgress` / `executing` / `complete`. For render-only actions (no frontend handler) the `executing` phase **never fires** — CopilotKit jumps directly from `inProgress` (with args streaming in via repeated deltas) to `complete`. So we treat `inProgress` and `executing` as a single "running" branch in `<ToolPill>`. A nice side effect: during arg streaming the pill updates progressively ("Searching apartments…" → "Searching K…" → "Searching Kreuzberg…") because each delta re-renders with the partial `args`.

**Registry-based copy.** `services/frontend/src/state/toolStatus.ts` holds one entry per backend tool name:

```ts
search_apartments: {
  executing: (a) => a.districts?.length
    ? `Searching ${a.districts.join(", ")}…`
    : "Searching apartments…",
  complete: (_a, result) => firstLine(result),     // "Found 12 listings, …"
},
```

The `executing` label is built from the tool's args (built up across `inProgress` deltas, final on `TOOL_CALL_END`). The optional `complete` label receives the tool's return value as `result` — our tools already shape their `return_value` so the first non-`Note:` line is label-worthy ("Found 12 listings…", "Page 2/3 — …"). Tools whose first return line is awkward (e.g. `get_result_details`'s `--- Listing #3 ---` banner) override `complete` directly. No structured wire contract beyond what AG-UI already streams.

Adding a new tool is **one entry here**. The wildcard registration already picks it up — no ChatPane edit needed. Backend tools never need to know about UI copy.

**Thinking phase.** A single `useCoAgentStateRender` renders a "Thinking…" pill that suppresses itself whenever any tool pill is currently `executing`. The cross-component signal is a tiny zustand counter (`useToolStatus.ts`'s `useActiveToolCount`) — same shape as `useHover`. The counter increments in `<ToolPill>`'s `useEffect` on `status === "executing"` and decrements on cleanup, so render functions stay pure.

**Lifecycle visible to the user:**

```
Thinking…           ← agent picking a tool, no tool active
Searching K…        ← status=executing, label from args
Found 12 in K       ← status=complete, label from first line of return
Thinking…           ← LLM writing the reply
(reply lands)       ← running=false, pill gone
```

**Why this and not `tool_logs` on `UiState`** (the previous design): putting status strings in shared state coupled tool code to UI copy, deleted on every search (lost history), and couldn't represent the "Thinking…" phase (no signal until the first tool finished). Tool-call lifecycle events are first-class in AG-UI and already streamed — the redesign just consumes what was already there.

**Upstream wire mechanics** — how `TOOL_CALL_*` events get from the agent to the SSE stream (and why `STATE_SNAPSHOT` is opt-in via `ToolReturn.metadata`, not auto-emitted) is documented in [`chat-runtime-and-streaming.md`](./chat-runtime-and-streaming.md). Read it before adjusting `chat/service.py`, `chat/tools.py`, or the nginx `/api/agent` block.

### What stays local (not in shared state)

- Map zoom/center (unless the user explicitly invokes a "search this area" action — deferred)
- Composer input draft
- Hover state on cards/markers (small client-local zustand store, not in `UiState`)

## Rejected alternatives

### Vercel AI SDK (`useChat`)
Speaks Vercel's UI Message Stream Protocol, not AG-UI. Bridging either direction throws away the integration we'd be picking Pydantic AI for in the first place. Tools, thinking, and typed shared state — all the features we want — go away.

### assistant-ui
Viable bailout if CopilotKit-on-Vite friction (issue #2340) blocks us. Cleaner Vite story and you own the components (shadcn-style). But shared state has no first-class primitive — assemble it yourself from `ExternalStoreRuntime` + Zustand. CopilotKit's `useCoAgent` is the single most important hook in this architecture; assistant-ui's equivalent requires manual wiring.

### Roll your own SSE + thread component
Re-invents markdown rendering, code blocks, copy buttons, thinking pills, tool-call pills, auto-scroll, message history, virtualization, edit-and-resend. Months of yak shaving.

### Mapbox GL JS v3
v2+ is proprietary, billed per Map Load, license auto-terminates on account lapse. No capability advantage over MapLibre for our use case.

### Leaflet
DOM/SVG rendering; no native vector tiles; weak for heatmaps and dynamic styling. Easy API but wrong tool for our heatmap/isochrone/clustered combo.

### deck.gl as primary map engine
deck.gl is a data-viz layer system, not a basemap. Add it as a MapLibre overlay later only when 3D price columns / animated flows / >100k point clouds justify it. Overkill for the marker volume we'll have.

### Zillow-style 3-pane with chat as a sidecar
Contradicts the product thesis. Incumbents are trapped in map-dominant 3-pane because their filter UI is their crown jewel; we have no such moat to protect, so the chat thread should host the page, not be a feature attached to it.

### Filter UI for MVP
A filter bar is an escape hatch from chat — users would take it because it's familiar, undermining the conversational thesis before we get to test it. Deferred; revisit only on observed friction. Per-cards-strip quick controls (sort, hide above €X) are a softer fallback if needed.

### Vite-side AG-UI integration via `@assistant-ui/react-ag-ui` alone
The runtime adapter works on Vite but reading `state.results` into the map and cards still needs an external store. Easier with CopilotKit's `useCoAgent` than assembling the same plumbing manually.

## Consequences

- **New backend file** `chat/ui_state.py`; **new backend route** `api/agent.py`. `ChatSession` and `ChatDeps` grow a `ui_state` field; `ChatService` exposes a `build_run_deps` helper for the AG-UI dispatch path. The existing REST `POST /api/conversations/{id}/messages` endpoint is removed.
- **State events are explicit, not automatic.** Pydantic AI's AG-UI adapter does *not* auto-emit `STATE_SNAPSHOT` / `STATE_DELTA` events when `deps.state` mutates — confirmed by reading `pydantic_ai/ui/ag_ui/_event_stream.py` (the imports list every event type except the state ones). Tools that mutate `state` must wrap their return in `ToolReturn(return_value=…, metadata=[StateSnapshotEvent(snapshot=state.model_dump())])`; the adapter yields any `BaseEvent` placed in `ToolReturn.metadata` into the stream alongside the tool result. `chat/tools.py` centralizes this via a `_return_with_state` helper. The full opt-in contract (and the rejected alternative of auto-emission) lives in [`chat-runtime-and-streaming.md`](./chat-runtime-and-streaming.md) §State emission is opt-in.
- **Nginx config** must add a `/api/agent` location block with SSE-safe settings (`proxy_buffering off`, `proxy_http_version 1.1`, `Connection ""`, `proxy_read_timeout 3600s`) and a `/tiles/` location with CORS + Range support for `.pmtiles`. The backend also sets `X-Accel-Buffering: no` belt-and-braces.
- **Frontend rewrite** under `services/frontend/src/`: new `App.tsx` chat-host layout, `state/UiState.ts` (manual TS mirror of the Pydantic model), `hooks/useUiState.ts` (single seam wrapping `useCoAgent`), and new components `ChatPane.tsx` / `MapPane.tsx` / `CardsPane.tsx` / `CardStrip.tsx` / `CardDetail.tsx`. The legacy `Chat.tsx` / `Chat.css` / `types.ts` are deleted.
- **Type sync chore**: `UiState` exists in Python (Pydantic) and TypeScript (manual mirror). No first-party codegen exists yet — keep them in sync manually for MVP; consider `pydantic-to-typescript` or a tiny in-repo codegen if the chore starts dragging.
- **Open issue**: CopilotKit-on-Vite paper cuts (CopilotKit issue #2340). Watch for it during the skeleton step; if it bites, the bailout to assistant-ui is documented above.
- **Pydantic AI version pin**: switching to `pydantic-ai-slim[ag-ui]` introduces a minimum version constraint (must include `pydantic_ai.ui.ag_ui.AGUIAdapter`). Pin to the latest stable at execution time.
- **Phoenix observability** transparently continues to capture AG-UI agent runs — `Agent.instrument_all()` already covers any agent.run path, and `using_session(session_id)` continues to tag spans.
- **The bet**: this design wagers that conversation is *better* than sliders for apartment search, not just novel. Fallback if the bet is wrong is cheap — add a slim filter bar, demote chat to a sidebar, keep all components — but our differentiation evaporates. Document this expectation so we don't accidentally erode it with feature creep on the filter side.

## See also

- `agent-compound-docs/conversations/frontend-stack-and-generative-ui.md` — long-form research archive: AG-UI sub-patterns, library shootouts, layout precedents (Zillow / Redfin / Idealista / Airbnb / Claude Artifacts), and the strategic "bet" framing
- `agent-compound-docs/decisions/chat-runtime-and-streaming.md` — endpoint design, how SSE is enabled, session store + locking, exception translation, `_return_with_state` opt-in contract, persistence on completion
- `agent-compound-docs/decisions/backend-architecture.md` — domain layering; `chat/`, `search/`, `api/` boundaries
- `agent-compound-docs/decisions/agent-framework.md` — why Pydantic AI
- `agent-compound-docs/decisions/llm-tool-result-design.md` — `ResultSet`'s prose/CSV/detail contract; `UiState` is a *parallel* projection, not a replacement
- `agent-compound-docs/decisions/architecture-diagram.md` — diagram is regenerated to show `/api/agent`, the Protomaps tiles volume, and CopilotKit on the frontend
