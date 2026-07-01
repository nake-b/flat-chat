# The lens layer — generalized map colouring

**Status:** Implemented in the travel-time PR (#41), generalizing the lens that
shipped hardwired to travel-time. Supersedes the "lens" description in
[`travel-time-routing.md`](travel-time-routing.md).

## Problem

A "lens" colours every map marker by one scalar — travel time to a place, and
(now) straight-line distance to a place. The first cut was hardwired to
travel-time: `_apply_travel_lens`, a `TravelTimeFilter`, a single `commute_min`
heatmap. Adding a second lens by copy-paste would double the special-casing. The
review of #41 asked for a real, documented lens *layer* — and for a second lens
from a **different data source** to prove the abstraction isn't coupled to
routing. Issue #42 is that distance lens.

## The shape

Two co-located concerns, the same semantics/appearance split as `MapOverlay`:

- **`MarkerLens{key, label}`** — the thin *descriptor* mirrored to the frontend.
  `key` names the active scalar; the frontend keys its colour ramp / number
  format off it in `state/lensStyles.ts`. `label` is the legend caption. The
  backend sets semantics only; the frontend owns appearance. Lives once in
  `SessionState`, never per marker.
- **`ActiveLens`** — the richer *input* the backend keeps to re-derive the lens
  after a follow-up `search_apartments` (which rebuilds markers from SQL and
  would otherwise drop the lens). A Pydantic **discriminated union** on `kind`:
  - `TravelTimeLens` — anchor + mode (transit/car) + optional minutes cutoff +
    transit schedule freshness.
  - `DistanceLens` — anchor + optional km cutoff.
  Both carry `near_place_ref` so the lens can be re-resolved on refinement (the
  distance provider re-reads the place's exact geometry from it).

Both live in the leaf `listings/lenses.py` (parallel to `listings/overlays.py`)
so `search/`, `routing/`, and `chat/` can all import them without a cycle.

`Marker.lens_value` carries the one active scalar per marker (warm rent by
default; commute minutes or distance metres under a lens).

## The provider interface

The generalization is a one-method protocol:

```python
async def resolve(markers: list[Marker], lens: ActiveLens) -> dict[str, float]
```

- `RoutingService.resolve` implements it for `TravelTimeLens` (OSRM car / MOTIS
  transit — fallible, raises `RoutingError`).
- `DistanceService.resolve` implements it for `DistanceLens` (PostGIS
  `ST_Distance` against the place's geometry — no engine, so it never raises a
  routing error).

`chat/lens_tools.py:_apply_lens` is the shared derivation: pick the provider by
`lens.kind` from a tiny per-request registry (`_provider_for`), annotate each
marker's `lens_value`, drop markers over the cutoff (in the provider's units —
minutes for travel, `max_km × 1000` metres for distance), refill the preview to
`PREVIEW_N`, and set the `MarkerLens` descriptor (`commute_min` / `distance_m`).
`search_apartments` calls `reapply_lens_hook` after each search, which wraps
`_apply_lens` and swallows `RoutingError` (drop the lens rather than fail the
search — the SQL result set is already valid).

## Add-a-lens recipe

1. A union member in `listings/lenses.py` (`kind` + anchor + its cutoff field).
2. A provider that returns `{marker_id: value}` (a service on `ChatDeps`).
3. A branch in `lens_tools._provider_for` / `_cutoff` / `_descriptor`.
4. A tool (`apply_<x>_lens`) in `chat/lens_tools.py` — validate the anchor via
   `PlaceService.anchor_point`, set `state.active_lens`, call `_apply_lens`.
5. A `LENS_STYLES` entry in `state/lensStyles.ts` keyed by the descriptor `key`
   (ramp / domain / number format) + a `state/toolStatus.ts` status-pill entry.
6. Mirror the union member in `state/SessionState.ts`.

## Why not one scalar field / two scalars at once

One lens is active at a time by design — you can't colour pins by two scalars at
once. So `SessionState` holds a single `active_lens`, not a list. `price_warm` is
NOT modelled as a lens (no `LENS_STYLES` entry) — with no lens active the map
renders plain pins. Dismissal is frontend-owned and kind-agnostic
(`merge_incoming_state`: the frontend may clear `active_lens`, never set one).

## Rejected

- **A `mode="distance"` on the travel tool.** Distance is a different data source
  (geometry, not routing) with different units and no schedule — folding it into
  the travel tool would muddy both. Separate tools, shared `_apply_lens`.
- **Routed (walking-network) distance for the distance lens.** Kept it
  straight-line precisely so the second lens exercises a non-routing provider.
  Walk-*time* is a separate follow-up (#49) via real routing.
