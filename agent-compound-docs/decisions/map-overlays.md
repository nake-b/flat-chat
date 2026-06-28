# Map geometry overlays — agent-drawn geometries + structural state emission

**Status:** Implemented in `feat/map-geometry-overlays` (off `feat/geo-context-v2`), June 2026. `StateEmittingToolset` + `merge_incoming_state` + `MapOverlay`/`map_overlays` + `PlaceService.overlay_geometry` + `TransitRouteService` + `show_on_map`/`clear_map_overlays` tools + frontend `OverlayLayer`/`overlayStyles`/`OverlayLegend` + unit/integration tests.

**Related docs:**
- [`session-state-design.md`](session-state-design.md) — the SessionState shape overlays extend
- [`named-place-search.md`](named-place-search.md) — `locate_place`/`near_place_ref`; overlays reuse the same `named_places` geometry, for *drawing* instead of `ST_DWithin`
- [`agent-vs-http-data-flow.md`](agent-vs-http-data-flow.md) — the AG-UI-vs-HTTP channel split; overlays ride the AG-UI state snapshot
- [`frontend-stack.md`](frontend-stack.md) — how `StateSnapshotEvent` reaches CopilotKit; status-pill registry the style registry mirrors
- [`spatial-neighbor-tables.md`](spatial-neighbor-tables.md) — why "near a U-line" is a *stop* filter, not a centerline one

## Problem

The map showed only apartment point-markers. We want the agent to **draw geometries** — the Spree (line), a lake/park/building (polygon), a U-Bahn line like the U7 (line), a Bezirk, the inside-the-ring zone — so users see listings *in relation to* real places. Three trigger modes: automatically when a search is anchored to a place/line, on explicit request ("draw the U8"), and proactively.

This surfaced a deeper question about the agent↔UI sync contract that the feature would stress from many call-sites.

## How state sync actually works (the ground truth)

Pydantic AI's AG-UI integration is **one-directional**: it validates the incoming envelope's `state` onto `deps.state` at run start (`pydantic_ai/ui/_adapter.py`) and does **nothing** to push `deps.state` mutations back. The *only* sanctioned way state reaches the UI mid-run is a `BaseEvent` placed in a tool's `ToolReturn.metadata`, which the adapter yields into the SSE stream (`pydantic_ai/ui/ag_ui/_event_stream.py`). CopilotKit replaces its local state from the `StateSnapshotEvent`. So the model is **shared-state**, not an imperative event channel — and we keep it that way (a second imperative "draw" channel would be a second source of truth that desyncs from the persisted state and breaks reload recovery).

Two footguns lived in this contract:
1. **Emit-or-forget (downstream).** A mutating tool had to both mutate `deps.state` *and* remember to wrap its return in a `StateSnapshotEvent`. Forget the second step → the agent and the persisted session stay in sync while the UI goes blind.
2. **Hand-rolled merge (upstream).** Frontend-owned fields were merged inline in dispatch; adding a field meant remembering to extend the merge or the write-back was silently dropped.

## Decisions

### 1. Structural emission via `StateEmittingToolset` (kills footgun #1)

A `WrapperToolset` (`chat/state_emission.py`) wraps the whole toolset and intercepts every `call_tool`: dump `deps.state` before, run the tool, dump after; if it changed, attach a `StateSnapshotEvent` to the result (rebuilding the `ToolReturn` so existing content/metadata survive; idempotent if the tool already emitted one). **Tool authors do exactly one thing — mutate `ctx.deps.state` — and emission can never be forgotten** because it lives in the wrapper, not the tool body. A per-tool decorator was *rejected*: a decorator you can forget is the same footgun one line up. Pure-query tools (`get_result_page`, `locate_place`) change nothing → emit nothing → no needless re-ship of the marker payload.

Routing is reliable: `CombinedToolset.call_tool` dispatches to `tool.source_toolset.call_tool` — the agent-registered wrapper — which delegates inward via `super().call_tool`. Verified end-to-end (a tool mutation produces a `STATE_SNAPSHOT` in the real SSE bytes) in `tests/unit/test_state_emission.py`.

### 2. `merge_incoming_state` formalizes ownership (tames footgun #2)

`chat/service.py:merge_incoming_state(persisted, incoming)` is the single edit-site for frontend-owned fields: `active_id`, `active_listing_detail`, and overlay **dismissals**. Persisted server state wins for everything else, so a malformed/stale push can't clobber agent-owned data.

### 3. Overlay ownership is bidirectional, by aspect

- **Existence/content = backend-owned.** The agent adds/replaces overlays.
- **Dismissal/visibility = frontend-owned.** The user hiding an overlay writes the *reduced* set back; the merge lets the incoming set only **shrink** the persisted one (intersect by id) — never add. Dismissal is therefore **sticky** (persists across turns) and **agent-visible** (`build_dynamic_state_prompt` lists drawn overlays in `<current_state>`, so the agent won't redraw something the user hid).

### 4. Semantics (backend) vs appearance (frontend)

`MapOverlay` carries only `id` / `kind` / `label` / `geojson` / `origin`. **Appearance lives entirely on the frontend** in `state/overlayStyles.ts`, keyed by `(kind, geometry type)` — solid colored transit lines (Berlin U/S-Bahn palette by label), translucent ring/bezirk/place-polygon fills, water-blue river lines. This mirrors how marker paint lives in `MapPane.tsx` and status labels in `toolStatus.ts`. No `style_hint` on the wire — it would be a second, drifting source of truth. (GTFS `route_color` exists in `world.transit_routes` but is intentionally *not* forwarded — keeping the backend semantics-only.)

### 5. Transit lines are a separate display-only path — NOT in `named_places`

Named places (Spree, parks, lakes, buildings) resolve via the existing `world.named_places` gazetteer — `PlaceService.overlay_geometry(place_ref)` returns the *same* geometry `near_place_ref` search uses, as simplified GeoJSON. Transit lines resolve via a new read-only `TransitRouteService` over `world.transit_routes`/`transit_route_shapes` (GTFS, already present from ingestion migration 0003 — **no ingestion/migration work needed**).

They are kept separate on purpose: a U-Bahn line does **not** belong in the search-near gazetteer. "Near the U8" means near a *stop served by* the U8 (matched via `listings_nearby_transit`), not near the line's centerline — a `near_place_ref` against a route polyline would match tunnel midpoints between stations. So transit geometry is display-only and the two never mix. The overlay concept unifies only at `SessionState.map_overlays` (source-agnostic GeoJSON).

### 6. Triggers + clear policy

- **Auto:** `search_apartments` rebuilds search-derived overlays from its spatial anchors (`near_place_ref` → place geometry; `transit.lines` → each line's geometry), mirroring the search tool's own asymmetry. These are `origin="search"` and **replaced** each search.
- **Explicit/proactive:** `show_on_map(place_ref | transit_line)` pins an overlay (`origin="pinned"`) that **survives** subsequent searches until `clear_map_overlays` or user dismissal.
- A pinned overlay with the same id as a fresh search overlay wins (sticky).

Geometry is simplified server-side (`ST_SimplifyPreserveTopology`, ~5 m tolerance + 5-digit coords; constants in `listings/context.py`) so the GeoJSON riding the snapshot stays small.

### 7. Place geometry resolution (`PlaceService.overlay_geometry`)

A `place_ref` rarely maps cleanly to one drawable shape, so resolution does three things (verified against live TU/FU Berlin data):

- **Tiebreak** — `locate` orders by `similarity, ST_Dimension DESC`, so a polygon/line beats a coincident point at equal name-match (a seed-alias POINT no longer outranks the real footprint).
- **Snap** — if the hit is a representative POINT (a seed alias like "TU Berlin"), snap to the nearest footprint (polygon/line, **any** kind) within `OVERLAY_SNAP_RADIUS_M` and use it as the anchor. The curated pin sits *on* its target, and the building's name never matches the alias ("Hauptgebäude der TU", not "TU Berlin"), so **proximity, not name**, is what finds it → "TU Berlin" draws the Hauptgebäude, "FU Berlin" the Rost-/Silberlaube, "Görli" the park. No footprint near → falls back to the point.
- **Cluster-union** — union the anchor's same-kind, **same-name** footprints within `OVERLAY_CLUSTER_RADIUS_M`, keeping only the richest dimension, via `ST_Union` (not `ST_Collect`, which yields an unclassifiable GeometryCollection when mixing POLYGON+MULTIPOLYGON). A campus fragmented into identically-named rows → its local cluster; a unique place → itself. *Rejected:* fuzzy-name union (a similarity floor swallows neighbours — "Berlin" alone scores 0.70) and canonical-name-from-alias-description (the campus buildings carry diverse function names, so it missed the actual building). The complete-campus assembly needs ingestion-side grouping (shared campus id) — out of scope.

## Rejected / deferred

- **Per-tool emit decorator** — rejected (forgettable; see decision 1).
- **`StateDeltaEvent` (JSON-Patch)** instead of full snapshots — a drop-in inside `StateEmittingToolset.call_tool` if snapshot size ever bites; v1 ships full snapshots.
- **Transit lines in `named_places`** — rejected (decision 5).
- **Bezirk / ring / proactive nearby-parks overlays** — `OverlayKind` reserves `bezirk`/`ring`/`parks`, but their resolvers (over `world.bezirke`/`inner_city_zone`, or per-listing nearby parks) are a fast-follow using the identical pattern; not in this PR.
- **Map viewport awareness** ("search where I'm looking") — needs viewport write-back; explicitly out of scope.
