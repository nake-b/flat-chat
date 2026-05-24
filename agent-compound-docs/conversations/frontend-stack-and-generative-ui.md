# Frontend Stack & Generative UI Discussion

Conversation about designing the frontend for the Berlin Apartment AI Assistant — chat UI, map, apartment cards, and how Pydantic AI's agent should drive all three through a single shared-state seam.

---

## Q1 — How should the chat UI integrate with Pydantic AI for streaming and generative UI?

**User:**

Let's think about the frontend — a nice chat interface, with Pydantic AI event streaming and UI integration. Let's make use of it. Also apartment cards. And a map.

**Assistant:**

Pydantic AI ships first-party support for the **AG-UI Protocol** (`pydantic_ai.ui.ag_ui.AGUIAdapter`) — an open, event-based wire format from the CopilotKit team. It standardises streaming, frontend tools, shared state, and custom events between agent backends and UIs. AG-UI emits typed events for everything the chat thread needs:

- Text: `TextMessageStart` / `Content` (delta) / `End` / `Chunk`
- Tool calls: `ToolCallStart` (with `toolCallName`) / `Args` (delta) / `End` / `Result`
- State: `StateSnapshot` (full), `StateDelta` (JSON Patch ops), `MessagesSnapshot`
- Reasoning/thinking: `ReasoningStart` / `MessageContent` / `End`
- Lifecycle: `RunStarted`, `RunFinished`, `RunError`, `StepStarted/Finished`
- `Raw` and `Custom` for escape hatches

The umbrella concept (agent decides what the UI shows) is called **Generative UI** — sometimes **Agentic UI**. Under that umbrella there are three distinct sub-patterns, often conflated:

| Pattern | What the agent emits | What the frontend does |
|---|---|---|
| **Inline component rendering** | A tool returns a component descriptor | Renders that component *inside the chat thread*. Vercel AI SDK and OpenAI's Apps SDK (ChatGPT widgets, Oct 2025) work this way. |
| **Shared state / CoAgents** | A tool mutates a typed state object; the protocol streams JSON Patch deltas alongside chat events | A central store ingests the patches; **sibling** components (map, cards, side panels) subscribe to slices. CopilotKit calls this **CoAgents**. |
| **Frontend tools / HITL** | The agent calls a tool that only exists in the browser (e.g. `panMapTo(lat,lng)`, `expandCard(id)`) | Browser executes it, returns result to the agent. Human-in-the-loop confirmations live here. |

For this product we want all three eventually. The MVP needs the first two; frontend tools are deferred (see Q5).

**Is the protocol standardised?** AG-UI is the closest thing — open spec, adopted first-party by Pydantic AI, LangGraph, Mastra, Agno. Vercel's AI SDK uses its own competing wire format. OpenAI's Apps SDK is a third. AG-UI is the only vendor-neutral, open option. It's young (mid-2024 origin), so "standardised" with one asterisk.

### Chat UI library options — tradeoff matrix

Five real candidates, ranked by fit:

**A. CopilotKit** (chosen)
- The team that wrote AG-UI. Provides `<CopilotKit>` provider, prebuilt `<CopilotChat>`, and the killer hooks: `useCoAgent({name, state})`, `useCopilotAction`, `useCoAgentStateRender`.
- Why it fits: `useCoAgent` is *exactly* the sibling-component pattern we want. First-party Pydantic AI integration. Stock-portfolio demo on their blog does our exact thing.
- Cons: Next.js-first DX (Vite works but has paper cuts — their issue #2340). Default `<CopilotChat>` UI is a chat-widget-in-a-box; full layout customisation possible but undertested. Pulls in a fair amount of opinionated framework.

**B. assistant-ui** (bailout option)
- A shadcn-style headless chat library. Compose `<Thread>`, `<Composer>`, `<Message>` primitives like Radix.
- Pros: cleanest Vite story, you own the components, excellent thread/markdown primitives, less vendor lock-in.
- Cons: shared state has no first-class primitive — you bridge it yourself via `ExternalStoreRuntime` + Zustand. Frontend tools require manual registration. Smaller community.

**C. Roll your own**
- Read SSE directly, render with a thread component you write.
- Pros: zero deps, total control.
- Cons: re-invent thinking pills, tool-call lifecycle, message-history persistence, scroll-to-bottom, virtualization, markdown, code blocks, copy buttons, auto-resizing composer. Each is a week of yak shaving. This is what people regret six months in.

**D. Vercel AI SDK (`useChat`)** — skip
- Speaks Vercel's UI Message Stream Protocol, not AG-UI. Bridging either direction throws away the integration we'd be picking Pydantic AI for in the first place.

**E. Chainlit / LangChain ChatUI / OpenAI Apps SDK** — skip
- Too opinionated or tied to other ecosystems. Chainlit owns the whole app. OpenAI Apps SDK only runs inside ChatGPT.

### Pick: CopilotKit

`useCoAgent` is the single most important primitive in our architecture and it's free with the framework. Bailout to assistant-ui is cheap if Vite friction bites.

### SSE through Vite dev + Nginx — gotchas

- Nginx defaults to `proxy_buffering on`, which buffers the entire SSE response. The `/api/agent` location must set `proxy_buffering off; proxy_cache off; proxy_http_version 1.1; proxy_set_header Connection ""; proxy_read_timeout 3600s;`.
- Belt-and-braces: send `X-Accel-Buffering: no` from the backend on the AG-UI response.
- Vite dev proxy is fine for SSE in practice; disable any global gzip middleware on the FastAPI side for the AG-UI route.

### Sources

- [Pydantic AI AG-UI integration docs](https://pydantic.dev/docs/ai/integrations/ui/ag-ui/)
- [AG-UI Protocol introduction](https://docs.ag-ui.com/introduction)
- [AG-UI events catalog](https://docs.ag-ui.com/concepts/events)
- [CopilotKit + Pydantic AI announcement](https://www.copilotkit.ai/blog/introducing-pydantic-ai-integration-with-ag-ui)
- [CopilotKit Pydantic AI docs](https://docs.copilotkit.ai/pydantic-ai/)
- [CopilotKit Vite issue #2340](https://github.com/CopilotKit/CopilotKit/issues/2340)
- [assistant-ui AG-UI runtime overview](https://www.assistant-ui.com/docs/runtimes/ag-ui/overview)
- [Vercel AI SDK UI overview](https://ai-sdk.dev/docs/ai-sdk-ui/overview)

---

## Q2 — What map stack for Berlin apartment search?

**User:**

I want a map of Berlin, showing apartment locations or different layers. What's the diff between MapLibre GL JS v5 and react-map-gl and deck.gl?

**Assistant:**

These are not alternatives — they are **layers in a stack** that often appear in the same `package.json`.

```
┌──────────────────────────────────────────────────┐
│  deck.gl  ← optional: data-viz layers overlaid   │
│           (heatmaps, hexagons, animated flows,   │
│            3D price columns)                     │
├──────────────────────────────────────────────────┤
│  @vis.gl/react-maplibre  ← React wrapper:        │
│  (or react-map-gl)         declarative <Map/>,   │
│                            <Source/>, <Layer/>   │
├──────────────────────────────────────────────────┤
│  MapLibre GL JS v5  ← the engine. WebGL          │
│                       rendering, vector tiles,   │
│                       clustering, basemap        │
└──────────────────────────────────────────────────┘
```

- **MapLibre GL JS** is the JS engine. Vanilla JS: `new maplibregl.Map({container})`. WebGL2-only and ESM-only as of v5.0 (Jan 2025). Does the heavy lifting: basemap, markers, GeoJSON sources, clustering, heatmap layers. You always need this layer.
- **`@vis.gl/react-maplibre`** is a thin declarative React wrapper. Under the hood it still drives MapLibre's imperative API. You want it because we're in React. vis.gl split the MapLibre half of `react-map-gl` into a dedicated package; for new MapLibre-only projects this is the right pick (cleaner deps, native TS types). `react-map-gl/maplibre` is the same code reached through a multi-target package — pick that only if you might swap to Mapbox.
- **deck.gl** is a separate WebGL framework focused on big-data visualization layers. Doesn't ship a basemap; composites onto MapLibre via `MapboxOverlay`. Overkill for hundreds–few-thousand markers; reach for it when you want a `HeatmapLayer`, `HexagonLayer`, or animated arcs.

Analogy: MapLibre is like Three.js (3D engine), react-map-gl is like react-three-fiber (React wrapper), deck.gl is like a specialized particle-system library composited into the Three.js scene.

### Library shootout

| Library | Verdict |
|---|---|
| **MapLibre GL JS v5** | Best fit. WebGL2-only, native vector tiles, built-in GeoJSON clustering, full style spec for custom layers, free + OSS. |
| **Mapbox GL JS v3** | Skip for an OSS-leaning project. v2+ is proprietary, billed per Map Load, license auto-terminates if account lapses. |
| **Leaflet** | DOM/SVG rendering. Easy API but no native vector tiles, chokes on heatmaps and dynamic styling. Wrong tool for our heatmap/isochrone/clustered combo. |
| **Deck.gl** | Excellent *additive* layer engine, not a basemap. Overkill for markers; add only when 3D / animation justifies it. |
| **OpenLayers** | Faster for huge static polygon/line counts in benchmarks, but React story is hand-rolled and ecosystem is smaller. Not worth the friction. |

### Tile source: Protomaps

Self-hosted Protomaps `.pmtiles` is the right default. OSM-derived, ODbL licence (attribution required), and you can `pmtiles extract` a Berlin bbox to keep the file in the tens-of-MB range. The full planet is ~120GB; a Berlin extract with `--maxzoom 14` is trivially CDN-hostable.

Practical wrinkles:
- Protomaps' built-in cartography is functional but plain. If we want prettier Berlin styling without hosting overhead, MapTiler Cloud's free tier is generous and its styles are drop-in MapLibre-compatible.
- Stadia Maps has a generous free tier but only for non-commercial use — flag if the project may ever monetize.
- Skip raw OSM raster tiles (tile.openstreetmap.org bans heavy app use).

### Clustering: MapLibre built-in

For dozens to a few thousand listings, MapLibre's native GeoJSON `cluster: true` source is the least painful path. `cluster_id` assigned automatically; unclustered points stay individually addressable for two-way card↔marker sync via `setFeatureState({source, id}, {hover: true})`. Reach for `supercluster` directly only if you need React DOM cluster markers (avatars, mixed-content tooltips).

### Isochrones (deferred)

Honest take: public-transit isochrones are the hard part of the spec.
- **openrouteservice** (free API, OSS, self-hostable) does car/bike/walk cleanly. Does *not* ship transit isochrones publicly.
- **Valhalla** can ingest GTFS (VBB publishes GTFS for Berlin) but community write-ups flag Berlin accuracy issues (~60 min where reality is 30–50 min).
- **GraphHopper** supports GTFS + isochrones with similar caveats.
- **Conveyal R5** is academically rigorous but self-host only.
- **TravelTime API** has best-in-class transit isochrones — paid for any real volume.

Pragmatic path for later: ship ORS car/bike/walk isochrones first; defer transit to a self-hosted Valhalla with VBB GTFS once users actually ask for it.

### v5 caveats worth knowing

- MapLibre v5 is **ESM-only / WebGL2-only**. Vite is fine; any old build tooling breaks.
- MapTiler SDK JS is a "MapLibre with batteries" alternative if we tire of wiring terrain/3D buildings by hand.
- OpenFreeMap and Versatiles are newer free tile hosts worth knowing about as Protomaps alternatives.

### Sources

- [MapLibre GL JS v5 release](https://github.com/maplibre/maplibre-gl-js/releases/tag/v5.0.0)
- [MapLibre clustering example](https://maplibre.org/maplibre-gl-js/docs/examples/create-and-style-clusters/)
- [Optimising MapLibre Performance for large GeoJSON](https://maplibre.org/maplibre-gl-js/docs/guides/large-data/)
- [react-maplibre docs (vis.gl)](https://visgl.github.io/react-maplibre/)
- [deck.gl with MapLibre](https://deck.gl/docs/developer-guide/base-maps/using-with-maplibre)
- [Protomaps basemap downloads](https://docs.protomaps.com/basemaps/downloads)
- [openrouteservice services](https://openrouteservice.org/services/)
- [Public transit isochrones with Valhalla (Berlin write-up)](https://bwnkl.de/posts/transit-isochrones/)

---

## Q3 — What layout? Chat-first or map-dominant?

**User:**

Let's brainstorm. The product thesis is conversational search. I want chat on the left, map on the right top, cards thin on right bottom — sharing the right side with the map. Clicking a card makes it take over all the card space.

**Assistant:**

This is a chat-host with a persistent map+cards artifact — the same layout grammar Claude Artifacts and ChatGPT Canvas use (chat-left, generated content right). No real-estate incumbent has shipped it yet.

### What the incumbents actually do

The **3-pane split-view** is the industry standard for desktop — but always map-dominant, never chat-dominant:

- **Zillow / Redfin**: filter bar top, map right (~50–60%), scrollable card grid left. Pan map auto-refreshes cards (bounding-box search). Redfin is the gold standard; Zillow's split view is "now an industry standard."
- **ImmoScout24 (DE)**: filter form first; results map-right / list-left, with polygon-draw. Uses Google Maps because users already know it.
- **Funda (NL)**: differentiates via three explicit toggles — card / list / map view.
- **Idealista (ES)**: map-centric. Shipped multi-area search Jan 2026 — pick non-adjacent neighborhoods.
- **Rightmove (UK)**: map top-right with draw-a-search and overlays (schools, transport).

The consensus: **map is the foundation, not a filter**, because location is the load-bearing decision criterion. Pricing pins turn the map into a market-density visualization.

### What AI-first sites are doing in 2025/2026

- **Redfin × Sierra** (Oct 2025): conversational search inside Redfin. 2× listings viewed, 47% more tour requests vs filter UI. But the chat is a **sidecar** — the existing map+list pane is still primary.
- **Zillow in ChatGPT** (Oct 2025): first real-estate app inside ChatGPT. Renders photos/maps/pricing inline as rich cards, then deep-links to zillow.com to convert.
- **Realtor.com in ChatGPT** (Mar 2026): targets pre-search — affordability, neighborhood comparison.

**No incumbent has fully inverted the layout.** Chat is always a sidecar or a federated answer, never the host. This is the opening.

### Layout decision

```
┌─────────────────────┬─────────────────────────────────┐
│                     │                                 │
│                     │             MAP                 │
│      CHAT           │   (always visible, ≈70%)        │
│      (~40%)         │   pricing pins, clustering      │
│                     │                                 │
│                     ├─────────────────────────────────┤
│                     │  CARDS (thin strip, ≈30%)       │
│                     │  ┌───┐ ┌───┐ ┌───┐ ┌───┐  →     │
│                     │  └───┘ └───┘ └───┘ └───┘        │
└─────────────────────┴─────────────────────────────────┘

When a card is clicked (Option X — cards grow, map shrinks):

┌─────────────────────┬─────────────────────────────────┐
│                     │                                 │
│      CHAT           │  MAP  (shrinks to ≈50%)         │
│                     │                                 │
│                     ├─────────────────────────────────┤
│                     │  CARD DETAIL (≈50%)             │
│                     │  Sibling cards hide; expanded   │
│                     │  card shows photos, full info,  │
│                     │  "back to results"              │
└─────────────────────┴─────────────────────────────────┘
```

- Map keeps full real estate even in detail view — you can see *where* this apartment is while reading about it.
- Cards-as-horizontal-strip feels less Zillow, more Netflix-row — fast left/right scan.
- In-place expansion preserves the mental model better than a drawer/modal — nothing flies in from off-screen.

### Tactical calls

1. **Auto-refresh on pan = off**, explicit "Search this area" button on. Panning shouldn't silently mutate the agent's active result set behind its back.
2. **Bidirectional highlight is non-negotiable** even at MVP — single cheapest signal that the two surfaces are one product.
3. **Pricing pins** deferred (see Q5) — plain circle markers for MVP.
4. **No mobile.** Berlin apartment hunting often happens on a phone, but we're scoped desktop-only (CLAUDE.md).
5. **No filter UI for MVP.** A filter bar is an escape hatch from chat — users would take it because it's familiar, undermining the thesis. Per-cards-strip quick controls (sort, hide above €X) are fine if needed; global filter chrome is not.

### The bet

There are **two distinct bets** in this design:

- **Bet A (technical)**: AG-UI shared state works and remains supported. Low risk — first-party in Pydantic AI, open protocol, cheap to fork.
- **Bet B (strategic)**: conversational search is the right primary interface for apartment hunting, with the map as a secondary visual artifact.

Bet B is the interesting one. Incumbents have a moat in their filter UI — decades of A/B-tested checkboxes, sliders, polygon-draw, saved searches. That's where their conversion is optimized. They cannot demote it without nuking their funnel; they're trapped in map-dominant 3-pane.

A green-field app has no such moat. We bet that "show me 2BR Kreuzberg under €1200 close to a U-Bahn, but with more light than the last one" is *genuinely better* than seven sliders — not just novel. Fallback if wrong: add a slim filter bar, demote chat to a sidebar. Cheap to fail back (same components, different layout container), but our differentiation is gone.

### Sources

- [Raw.Studio — Maps as Core UX in Real Estate](https://raw.studio/blog/using-maps-as-the-core-ux-in-real-estate-platforms/)
- [Map UI Patterns — Search this area](https://mapuipatterns.com/search-this-area/)
- [Redfin + Sierra conversational search](https://www.geekwire.com/2025/redfin-launches-chatbot-providing-conversations-with-home-shoppers-moxiworks-releases-new-platform/)
- [Zillow ChatGPT app](https://www.zillow.com/news/zillow-becomes-first-real-estate-app-in-chatgpt/)
- [Realtor.com ChatGPT integration (Mar 2026)](https://www.realestatenews.com/2026/03/30/realtor-com-the-latest-portal-to-launch-search-app-in-chatgpt)
- [Smashing Magazine — Modal vs Separate Page decision tree](https://www.smashingmagazine.com/2026/03/modal-separate-page-ux-decision-tree/)
- [Claude Artifacts side-panel pattern](https://support.claude.com/en/articles/9487310-what-are-artifacts-and-how-do-i-use-them)
- [UX Collective — Where should AI sit in your UI?](https://uxdesign.cc/where-should-ai-sit-in-your-ui-1710a258390e)

---

## Q4 — Where does shared state live, and how does it relate to ResultSet?

**User:**

I love the shared-state approach. Where does this state live, who maintains it? Backend or frontend? And is it the same thing as our existing `ResultSet`?

**Assistant:**

**Backend-authoritative. Frontend mirrors.** This is the canonical AG-UI flow.

```
┌─────────────────────────────────────┐
│  Backend (authoritative)            │
│  ───────────────────────────────    │
│  StateDeps[UiState] attached to     │
│  the agent run. Tools mutate it.    │
│  This is "the truth."               │
└────────────────┬────────────────────┘
                 │ JSON Patch deltas
                 │ over SSE (AG-UI)
                 ▼
┌─────────────────────────────────────┐
│  Frontend (mirror)                  │
│  ───────────────────────────────    │
│  CopilotKit store applies patches.  │
│  React components read via          │
│  useCoAgent(). Read-only by default;│
│  setState() sends an update back to │
│  the agent.                         │
└─────────────────────────────────────┘
```

### Why backend-authoritative

- The agent needs to **reason about** what's displayed. "Show me the cheapest one" requires the LLM to read `state.results`. If the frontend owned state, the agent would be blind.
- **Persistence and replay**: persist `(messages, state_snapshots)` and you can reload a conversation including its visual state. Conversations become Postgres rows with the map repopulated on refresh.
- **Single source of truth**: no race conditions about which side won a click.

### Relationship to ResultSet

`UiState` is **not the same** as `ResultSet`. Two distinct objects with shared source data:

| | `ResultSet` (backend, exists today) | `UiState` (new) |
|---|---|---|
| Purpose | Format apartment data **for the LLM** | Carry apartment data **for the UI** |
| Owners | LLM (reads via tool returns + navigation footers) | React components (map, cards) |
| Shape | Prose summary, CSV bulk page, prose detail, navigation footer | List of typed apartment objects with lat/lng, active_id, etc. |
| Lives in | `chat/state.py` | `chat/ui_state.py` |

Practical wiring inside a tool:

```python
@toolset.tool
async def search_apartments(ctx: RunContext[ChatDeps], query: str) -> str:
    apartments = await ctx.deps.search_service.search(params)
    ctx.deps.session.result_set = ResultSet(df=apartments, params=params)
    ctx.deps.ui_state.results = [
        UiApartment.from_dataframe_row(row) for _, row in apartments.iterrows()
    ]
    ctx.deps.ui_state.active_id = None  # reset on fresh search
    return ctx.deps.session.result_set.summary()  # LLM-facing prose stays the same
```

One tool call, two projections. The LLM keeps reading prose; the UI gets a structured list to render with.

### What goes in shared state, what stays local

**In shared state** — anything the agent might want to reason about:
- `results` — list of apartments currently displayed
- `active_id` — which card/marker is expanded
- `tool_logs` — rolling tool-call lifecycle for inline pills

**Local React state only** — pure rendering ephemera the agent doesn't care about:
- Current map zoom/center (unless user explicitly says "search this area")
- Composer input draft
- Which photo of a carousel is visible
- Hover state on cards/markers

### Write-back path

When the user clicks "expand card #3", the frontend calls `setState({ active_id: 3 })`. CopilotKit streams this back to the backend; the agent's next turn sees `ctx.deps.ui_state.active_id == 3` and can reason "the user is looking at the Kreuzberg listing." This is how a chat-host product stays *aware* — the agent always knows what the user is staring at.

### How people *usually* do this (the alternatives)

| Approach | Idea | Problem |
|---|---|---|
| **Side-channel parsing** | Frontend parses tool-call JSON out of the chat stream | Brittle; tight coupling between LLM output format and UI; every new tool needs new parsing |
| **God component** | `<ChatWithMap>` owns everything, prop-drilled | 2000-line file in a month, hard to test |
| **Event bus** | Chat fires events, map subscribes | No source of truth; refresh → empty map; agent doesn't know what user sees |
| **Shared state (AG-UI)** | Typed state object on backend, JSON Patch deltas to frontend store | Single source of truth, type-safe, agent-introspectable |

### Why elegant

- **Single source of truth.** No parsing JSON out of messages, no manual extraction.
- **Type-safe end-to-end.** Pydantic `UiState` matches a TS type; add a field, both ends know.
- **JSON Patch deltas.** Wire payload for "add one apartment" is `{op:'add', path:'/results/-', value:{...}}` — tiny.
- **Decoupling for free.** Want a comparison panel? New component subscribes to a new state slice. Chat and map don't touch.
- **Agent introspection.** The LLM can call `get_current_view()` against `ctx.deps.ui_state` — agent and UI agree on reality.
- **Replay / persistence.** `(messages, state_snapshots)` persisted; rehydrate a conversation with map+cards restored.

### Honest cons

- **AG-UI early-adopter risk.** Protocol is ~18 months old. If it loses to Vercel's format we re-wire. Open-protocol means worst case is fork-the-adapter.
- **TS/Pydantic type sync chore.** No first-party codegen yet — manual duplication or a small script. Acceptable for MVP.
- **State design judgment.** Wrong calls → either over-chatty wire (everything in shared state) or lost state on reload (too much client-local).
- **Debugging.** State changes happen out-of-band of chat messages; need devtools or logging.

### What `useCoAgent` looks like

```tsx
const { state, setState } = useCoAgent<UiState>({
  name: 'berlin-agent',
  initialState: { results: [], active_id: null, tool_logs: [] },
});
```

`state` re-renders the component when the agent mutates its server-side state. No SSE parsing, no `useEffect` plumbing, no Redux wiring. That's the "first-class primitive" — a core hook, not something built on top of lower-level streaming events. The equivalent in assistant-ui exists but requires assembling it from `ExternalStoreRuntime` + a state manager.

### Sources

- [Pydantic AI AG-UI state docs](https://pydantic.dev/docs/ai/integrations/ui/ag-ui/#shared-state)
- [CopilotKit CoAgents docs](https://docs.copilotkit.ai/coagents/concepts/agent-state-streaming)
- [AG-UI StateDelta event](https://docs.ag-ui.com/concepts/events#state-events)

---

## Q5 — What's deferred?

**User:**

Don't bake everything into MVP. What can we punt?

**Assistant:**

Pinned to the post-MVP list:

- **Agent-callable frontend tools** (Generative-UI pattern 3) — `pan_map_to(lat, lng)`, `expand_card(id)`, `highlight_kiez(name)` exposed via CopilotKit's `useCopilotAction`. Powerful for "zoom to where I'm looking" or guided tours; only worth doing after the chat ↔ map shared-state loop is solid.
- **Pricing pins** — Berliners scan for price first, plain markers waste glanceable pixels, and clusters could show price *range* not just count. Cheap to implement (MapLibre supports HTML/SVG markers, CSS sizes them by digits). Deferred only to keep MVP scope tight; revisit immediately after end-to-end is working.
- **Filter UI** — undermines conversational thesis at MVP; revisit only after observing real user friction. Per-cards-strip quick controls (sort, hide above €X) are a softer fallback if needed.
- **Public transit isochrones** — genuinely hard in Berlin (Valhalla+VBB GTFS accuracy issues). Ship ORS car/bike/walk first when isochrones are wanted; defer transit until users ask for it.
- **Mobile / responsive layouts** — desktop-only product (CLAUDE.md "Out of Scope").
- **Map auto-pan-refresh ("search this area")** — explicit button only at MVP. Even later, auto-refresh should never silently mutate the agent's active result set.
- **TypeScript codegen from Pydantic `UiState`** — manual duplication for MVP. Add `pydantic-to-typescript` or a small in-repo codegen if the manual sync starts dragging.
- **DB-backed `SessionStore`** — `InMemorySessionStore` is fine for MVP; swap when conversations need to survive backend restarts.

All of these are noted in `CLAUDE.md` under "Deferred / nice-to-have (post-MVP)".
