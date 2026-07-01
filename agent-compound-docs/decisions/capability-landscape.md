# Capability landscape — how tools are grouped

**Status:** Implemented in the travel-time PR (#41), splitting the single
`ListingsCapability` into domain capabilities as the tool surface grew. Extended
in #44, which added `ListingProximityCapability` as the first `defer_loading=True`
capability (single-listing distance / travel-time point queries).

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
| **ListingProximityCapability** (`chat/tools/proximity.py`) | `distance_to`, `travel_time_to` (single listing → one place) | late-session, evaluating one flat — often never | **deferred** |

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

## defer_loading: the always-loaded set stays undeferred; proximity defers

The always-loaded three (Core / MapOverlay / Lens, ~10 tools) stay in the cached
prefix — the prefix is comfortable (~5,600 tokens) and deferring them buys little
against a first-use round-trip. **`ListingProximityCapability` is the one we
defer** (#44): it's the textbook case — a late-session, single-listing question
(asked only while evaluating one specific flat) that many conversations never
trigger — so its two tools + `<proximity_protocol>` prose stay OUT of the cached
prefix until the model loads the capability on demand.

Mechanics (verified against the installed Pydantic AI): `defer_loading=True`
requires a stable `id` (message history identifies the capability across a load)
and a `description` (the load-catalog routing hint the model reads to decide
whether to load). Turning on ANY deferred capability injects two plumbing tools
into the surface — `load_capability` (loads a capability by id) and `search_tools`
— and flags the deferred tools with `metadata['pydantic_ai_deferred_capability_tool']`.
The deferred-capability catalog renders as a *static* instruction (byte-identical
every turn, including after a load) precisely so it doesn't bust the prompt-cache
prefix. `test_capabilities_wiring.py` asserts all of this: always-loaded tools
present + un-deferred, proximity tools present + flagged, plumbing present.

The rule for the always-loaded set is unchanged: flip `defer_loading` when that
count crosses ~10–12 or tool selection gets sloppy. **Capabilities are the seam;
`defer_loading` is the lever — and proximity is the first place we pulled it.**

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
