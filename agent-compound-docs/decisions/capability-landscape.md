# Capability landscape — how tools are grouped

**Status:** Implemented in the travel-time PR (#41), splitting the single
`ListingsCapability` into domain capabilities as the tool surface grew.

## Context

Pydantic AI v2 binds tools to the agent via `capabilities=[...]`. We started with
one `ListingsCapability` wrapping every tool. As the surface grew (search, map
overlays, and now two lenses + the planned single-listing distance/travel
queries), one flat `tools/core.py` with one `<tool_protocol>` blob started to hurt.

## The de-risking fact

Splitting tools into multiple capabilities is **behavior-neutral to the LLM** —
*unless* you set `defer_loading`. Pydantic AI concatenates every capability's
tools and instructions into the same flat prompt. So the split is purely internal
code organization + a future lever; it cannot regress tool selection on its own
(as long as the concatenated instructions stay coherent). This lets us split
freely and reversibly.

## The landscape

| Capability | Tools | When used | Loaded? |
|---|---|---|---|
| **CoreCapability** (`chat/tools/core.py`) | `search_apartments`, `open_listing`, `get_result_page`, `locate_place` | every conversation | always |
| **MapOverlayCapability** (`chat/tools/overlays.py`) | `show_on_map`, `hide_on_map`, `clear_map_overlays` | orienting on the map | loaded |
| **LensCapability** (`chat/tools/lenses.py`) | `apply_travel_time_lens`, `apply_distance_lens`, `clear_lens` | mid-session, commute/distance | loaded |
| **ListingProximityCapability** *(follow-up, [#44](https://github.com/nake-b/flat-chat/issues/44))* | `distance_to`, `travel_time_to` (single active listing) | late-session, evaluating one flat — often never | **deferred** |

Each capability's `get_toolset()` wraps its own `FunctionToolset` in
`StateEmittingToolset`, so any `deps.state` mutation still auto-emits a
`STATE_SNAPSHOT` (per-call diffing works identically across three wrappers).

The boundaries map to **load-frequency**, which *is* the `defer_loading`
decision — cohesion and defer-ability line up, a sign the boundary is right.

## Why these boundaries

- **`locate_place` lives in Core**, not with overlays/lenses, because it mints
  the `place_ref` tokens every other capability consumes — it must always be
  loaded even if a consuming capability is deferred.
- **Lens vs. single-listing queries are separate capabilities** even though they
  share `RoutingService`/`DistanceService`/`PlaceService`. Capabilities group
  *tools + protocol text only* — services live on `ChatDeps`, shared by all — so
  the split costs no duplication, and it lets the rarely-used point-queries be
  deferred without dragging the lens tools out of the cached prefix.

## Shared backbone

Cross-capability invariants — the one-result-set model, 1-based indices, and the
`place_ref` flow — live in `chat/tools/backbone.py:TOOL_BACKBONE`, appended to the
agent's static `instructions=` (so they sit in the cached prefix). Each
capability's own `<..._protocol>` then describes only its own tools. This is
where "run `locate_place` first" is documented once, spanning capabilities.

## defer_loading: not yet

The cached prefix is comfortable (~5,600 tokens). Deferring now buys little and
adds a first-use ToolSearch round-trip. We split into capabilities now so
`defer_loading` is a one-line flip later — when the always-loaded tool count
crosses ~10–12 or tool selection gets sloppy. **Capabilities are the seam;
`defer_loading` is the lever.**

## The bigger picture: package-by-layer vs. package-by-feature

The backend is packaged **by layer** (`api → chat → search/routing → listings →
core`), which buys an enforceable acyclic dependency graph (`listings/` is a
reusable leaf). A *feature* (overlays, lenses) is a vertical cut across those
layers — and a file tree can only encode one hierarchy, so we keep layers as the
primary axis and recover feature cohesion with connective tissue:

- a **shared-kernel leaf** module per feature (`listings/overlays.py`,
  `listings/lenses.py`) — the single place the vocabulary is defined;
- **capabilities** as the feature-axis grouping *within* the chat layer (tools +
  their protocol co-located);
- consistent naming (`overlay*` / `lens*`) as a cross-index;
- ADRs (this file, `map-overlays.md`, `lens-layer.md`) as the map the tree can't
  draw.

See [`domain-context-map.md`](domain-context-map.md) for the bounded-context view.
