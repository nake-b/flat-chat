"""LensCapability — colour the map by a scalar (travel time OR distance).

A map LENS annotates every marker's `lens_value` and, with a cutoff, drops the
rest; one lens is active at a time. Two lenses today, from two different data
sources — proving the abstraction is provider-agnostic:
  - travel time → `RoutingService` (OSRM car / MOTIS transit)
  - straight-line distance → `DistanceService` (PostGIS, no engine)

Both providers expose `resolve(markers, lens) -> {id: value}`, so `_apply_lens`
treats them interchangeably via a tiny registry keyed on the lens `kind`. Adding
a lens = a union member in `listings/lenses.py` + a provider + a `lensStyles.ts`
entry + a tool here. See `agent-compound-docs/decisions/lens-layer.md`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic_ai import FunctionToolset, ModelRetry, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AgentToolset

from flat_chat.chat.overlay_tools import _upsert_overlay
from flat_chat.chat.state import ChatDeps
from flat_chat.chat.state_emission import StateEmittingToolset
from flat_chat.listings.lenses import ActiveLens, DistanceLens, TravelTimeLens
from flat_chat.routing.errors import RoutingError
from flat_chat.search.schemas import PREVIEW_N

logger = logging.getLogger(__name__)

toolset: FunctionToolset[ChatDeps] = FunctionToolset()


_LENS_PROTOCOL = """\
<lens_protocol>
Map LENSES colour every listing by ONE scalar (one lens active at a time). Run a
search first — a lens annotates/filters the ACTIVE result set; it does NOT search.
The lens sticks across later searches until changed or cleared.
  - `apply_travel_time_lens(near_place_ref=…, mode=…, max_minutes=…)` — colour by
    TRAVEL TIME (transit or car) to a place, and with `max_minutes` drop listings
    over the cutoff. For "near my work at TU Berlin", "≤30 min by U-Bahn from
    Alex", "how's the drive to the airport?".
  - `apply_distance_lens(near_place_ref=…, max_km=…)` — colour by STRAIGHT-LINE
    distance to a place (bird's-eye, no routing), and with `max_km` drop the rest.
    For "how far is each from the Spree?", "within 2 km of TU Berlin".
  - `clear_lens()` — remove the active lens, recolouring back to default pins.
    Recolour only; listings a cutoff dropped come back only on a new search.

Choosing: "how long / commute / drive / by U-Bahn" → travel-time lens (transit
default; car for "drive"/"by car"). "how far / distance / how many km" → distance
lens. A stated limit ("under 30 min", "within 2 km") → pass the cutoff
(`max_minutes` / `max_km`); "I care about it" / "show me how far" → omit it
(colour + annotate only). The user can also dismiss the lens in the UI (the × on
the legend).
</lens_protocol>

<lens_phrase_map>
  - "≤30 min by U-Bahn from
    TU Berlin" / "within 25 min
    transit of my work"         → locate_place(…) → apply_travel_time_lens(
                                  near_place_ref=…, mode="transit", max_minutes=30)
  - "max 20 min drive to the
    airport"                    → locate_place(…) → apply_travel_time_lens(
                                  near_place_ref=…, mode="car", max_minutes=20)
  - "I work at TU Berlin, show
    me the commute" / "how long
    is each from …"             → locate_place(…) → apply_travel_time_lens(
                                  near_place_ref=…, mode="transit")  # no cutoff
  - "how far is each from the
    Spree" / "distance to
    TU Berlin"                  → locate_place(…) → apply_distance_lens(
                                  near_place_ref=…)  # no cutoff
  - "within 2 km of TU Berlin"  → locate_place(…) → apply_distance_lens(
                                  near_place_ref=…, max_km=2)
  - "remove the lens" / "stop
    colouring" / "back to normal" → clear_lens()
</lens_phrase_map>
"""


@toolset.instructions
def lens_protocol_instructions() -> str:
    return _LENS_PROTOCOL


# --- Provider registry + shared derivation ---------------------------------


def _provider_for(ctx: RunContext[ChatDeps], kind: str):
    """The lens value provider for a lens kind. Both expose the same
    `resolve(markers, lens) -> {id: value}` shape, so `_apply_lens` is agnostic."""
    if kind == "travel_time":
        return ctx.deps.routing_service
    if kind == "distance":
        return ctx.deps.distance_service
    raise ValueError(f"no provider for lens kind {kind!r}")


def _cutoff(lens: ActiveLens) -> float | None:
    """The hard cutoff in the PROVIDER's units (minutes for travel, metres for
    distance — DistanceService returns metres, `max_km` is km), or None."""
    if lens.kind == "travel_time":
        return lens.max_minutes
    return None if lens.max_km is None else lens.max_km * 1000.0


def _clear_lens(ctx: RunContext[ChatDeps]) -> None:
    """Reset to the default price view: drop the active lens and remove the
    anchor overlay the lens drew.

    `marker_lens` needs no reset — it's computed from `active_lens` (now None →
    `price_warm`). Only overlays with `origin="lens"` are removed, so user pins
    and search overlays are untouched. Recolour-only — the result set (including
    any listings a cutoff dropped) is left as-is; a new search restores it.
    Shared by the `clear_lens` tool, the frontend dismissal (honoured in
    `merge_incoming_state`), and `_apply_lens`'s no-lens branch."""
    state = ctx.deps.state
    state.active_lens = None
    state.map_overlays = [o for o in state.map_overlays if o.origin != "lens"]


async def _refresh_result_set(ctx: RunContext[ChatDeps]) -> None:
    """Re-run the active search (`state.search_params`) to rebuild the FULL result
    set before a lens is (re)applied on demand.

    Why: a lens cutoff drops markers destructively. Without this, applying a new
    lens (e.g. switching the anchor) would filter the PREVIOUS lens's leftovers,
    compounding the filters (the "FU ∩ TU" leak). Re-deriving from the search
    filters makes each apply `(search) ∩ (this lens)`. Leaves `active_id` /
    `active_listing_detail` untouched (the user's focus survives)."""
    state = ctx.deps.state
    # Callers guard `search_params is None` before invoking (can't re-derive
    # without the query); assert narrows the Optional for the type checker.
    assert state.search_params is not None
    markers, preview, total, facets = await ctx.deps.search_service.search(
        state.search_params
    )
    state.result_markers = markers
    state.preview_cards = preview
    state.total_results = total
    state.facets = facets


async def _draw_lens_anchor(ctx: RunContext[ChatDeps], near_place_ref: str) -> None:
    """Draw the lens's anchor as an `origin="lens"` overlay, ownership-by-creation.

    Removes any previous lens anchor first (switching anchors), then draws the new
    one — but only if the place isn't ALREADY on the map (a user pin / search
    overlay of the same place is borrowed, not claimed, so clearing the lens later
    won't remove it)."""
    state = ctx.deps.state
    state.map_overlays = [o for o in state.map_overlays if o.origin != "lens"]
    overlay = await ctx.deps.place_service.overlay_geometry(
        near_place_ref, origin="lens"
    )
    if overlay is not None and all(o.id != overlay.id for o in state.map_overlays):
        _upsert_overlay(state, overlay)


async def _apply_lens(ctx: RunContext[ChatDeps]) -> None:
    """Annotate `lens_value` (and apply the cutoff) from the active lens.

    `marker_lens` is NOT set here — it's a computed field on `SessionState`,
    derived from `active_lens`. Shared by `search_apartments` (re-apply after a
    refinement, via
    `reapply_lens_hook`) and the `apply_*_lens` tools. Keeps the search's sort
    order, only annotating each marker with the lens value and — when a cutoff is
    set — dropping the ones over it. Filtering preserves order; the preview is
    refilled back up to `PREVIEW_N` (a cutoff can drop cards that were in the
    original top-N), so `preview_cards` stays a true, full-length prefix of
    `result_markers`.

    No lens → resets to the default `price_warm` lens (markers keep the price
    value search already wrote). Providers may raise (e.g. `RoutingError`)."""
    state = ctx.deps.state
    lens = state.active_lens
    if lens is None:
        _clear_lens(ctx)
        return

    provider = _provider_for(ctx, lens.kind)
    value_by_id = await provider.resolve(state.result_markers, lens)
    cutoff = _cutoff(lens)

    new_markers = []
    for m in state.result_markers:
        value = value_by_id.get(m.id)
        if cutoff is not None and (value is None or value > cutoff):
            continue  # over the cutoff (or unreachable) → drop
        new_markers.append(m.model_copy(update={"lens_value": value}))

    state.result_markers = new_markers
    state.total_results = len(new_markers)

    # Rebuild the preview as the true prefix of the (filtered) marker order,
    # refilled to PREVIEW_N. A cutoff can drop cards that were in the original
    # top-N and promote markers from beyond the preview window; those carry no
    # card data (markers are thin), so hydrate the newly-promoted ids by id.
    preview_ids = [m.id for m in new_markers[:PREVIEW_N]]
    by_id = {c.id: c for c in state.preview_cards}
    missing = [i for i in preview_ids if i not in by_id]
    if missing:
        by_id.update(
            {c.id: c for c in await ctx.deps.listing_service.get_cards(missing)}
        )
    state.preview_cards = [by_id[i] for i in preview_ids if i in by_id]


async def reapply_lens_hook(ctx: RunContext[ChatDeps]) -> None:
    """Post-search hook invoked by `search_apartments`: re-apply the active lens
    to the fresh result set so a refinement keeps its heatmap/filter.

    Owns the graceful-degradation policy: routing is a fallible external
    dependency, so if it's down during a refinement, drop the lens rather than
    failing the whole search — the SQL result set is already valid. (Distance is
    SQL, so it doesn't raise `RoutingError`.)"""
    try:
        await _apply_lens(ctx)
    except RoutingError as exc:
        logger.warning("travel lens re-apply failed during refinement: %s", exc)
        _clear_lens(ctx)


# --- Tools ------------------------------------------------------------------


@toolset.tool
async def apply_travel_time_lens(
    ctx: RunContext[ChatDeps],
    near_place_ref: str,
    mode: str = "transit",
    max_minutes: int | None = None,
) -> str:
    """Add a travel-time (commute) lens to the ACTIVE result set.

    Run `search_apartments` first — this annotates / filters the listings that
    search already found; it does not search. Recolours the map pins by travel
    time to `near_place_ref` (bold = closer, faded = farther) and shows the
    minutes on each card.

    Args:
        near_place_ref: A `place_ref` from `locate_place` for the destination
            (e.g. the user's workplace / university / a landmark).
        mode: "transit" (public transport, default) or "car" (driving).
        max_minutes: If set, DROP listings further than this many minutes (a
            hard commute filter). If omitted, keep all listings and only
            colour/annotate by travel time.

    The lens persists across later `search_apartments` refinements until the
    user changes the destination/mode or it's cleared.
    """
    state = ctx.deps.state
    if state.search_params is None or not state.result_markers:
        return (
            "There's no active result set yet. Run search_apartments first, "
            "then I can add a travel-time lens to those listings."
        )

    anchor = await ctx.deps.place_service.anchor_point(near_place_ref)
    if anchor is None:
        raise ModelRetry(
            f"Could not resolve place_ref {near_place_ref!r}. Call locate_place "
            "first and pass one of the returned place_ref tokens."
        )

    # Reset to the full search result BEFORE applying, so switching anchors (or
    # re-applying) filters (search ∩ this lens), never a prior lens's leftovers.
    await _refresh_result_set(ctx)

    lens_mode = "car" if mode == "car" else "transit"
    state.active_lens = TravelTimeLens(
        anchor_label=anchor.label,
        anchor_lat=anchor.lat,
        anchor_lng=anchor.lon,
        near_place_ref=near_place_ref,
        mode=lens_mode,
        max_minutes=max_minutes,
    )

    # Draw the destination as the lens's OWN anchor overlay (origin="lens"), so
    # clearing/switching removes exactly it and nothing the user pinned.
    await _draw_lens_anchor(ctx, near_place_ref)

    try:
        await _apply_lens(ctx)
    except RoutingError as exc:
        # Routing is a fallible external dependency: drop the lens (+ its anchor
        # overlay) and tell the agent so it can offer to proceed without it.
        _clear_lens(ctx)
        logger.warning("apply_travel_time_lens failed: %s", exc)
        return (
            f"I couldn't reach the {lens_mode} routing service to compute travel "
            f"times to {anchor.label}. The listings are unchanged — want me to "
            "continue without the commute filter?"
        )

    how = "driving" if lens_mode == "car" else "transit"
    # If the transit timetable has lapsed, the routing layer clamped the departure
    # into the last covered day and flagged it — tell the user the schedule's age.
    lens = state.active_lens
    stale_note = ""
    if (
        isinstance(lens, TravelTimeLens)
        and lens.schedule_stale
        and lens.schedule_as_of
    ):
        stale_note = (
            f" Note: the transit timetable data only runs through "
            f"{lens.schedule_as_of}, so these times reflect that day's schedule."
        )
    if max_minutes is not None:
        return (
            f"Filtered to {state.total_results} listings within {max_minutes} "
            f"min {how} of {anchor.label}. The map is now coloured by travel time "
            f"(bold = closer, faded = farther).{stale_note}"
        )
    return (
        f"Coloured the map by {how} time to {anchor.label} (bold = closer, faded "
        f"= farther). All {state.total_results} listings are still shown."
        f"{stale_note}"
    )


@toolset.tool
async def apply_distance_lens(
    ctx: RunContext[ChatDeps],
    near_place_ref: str,
    max_km: float | None = None,
) -> str:
    """Add a straight-line (bird's-eye) distance lens to the ACTIVE result set.

    Run `search_apartments` first — this annotates / filters the listings search
    already found; it does not search. Recolours the map pins by straight-line
    distance to `near_place_ref` (bold = closer, faded = farther) and shows the
    km on each card. This is geometry only (no routing), so it's the right tool
    for "how far", NOT "how long" (use `apply_travel_time_lens` for time).

    Args:
        near_place_ref: A `place_ref` from `locate_place` for the destination.
            Distance is measured to the place's exact shape (a river line, a
            campus polygon), not just its centre.
        max_km: If set, DROP listings farther than this many km. If omitted, keep
            all listings and only colour/annotate by distance.

    The lens persists across later `search_apartments` refinements until changed
    or cleared.
    """
    state = ctx.deps.state
    if state.search_params is None or not state.result_markers:
        return (
            "There's no active result set yet. Run search_apartments first, "
            "then I can add a distance lens to those listings."
        )

    anchor = await ctx.deps.place_service.anchor_point(near_place_ref)
    if anchor is None:
        raise ModelRetry(
            f"Could not resolve place_ref {near_place_ref!r}. Call locate_place "
            "first and pass one of the returned place_ref tokens."
        )

    # Reset to the full search result BEFORE applying (see apply_travel_time_lens).
    await _refresh_result_set(ctx)

    state.active_lens = DistanceLens(
        anchor_label=anchor.label,
        anchor_lat=anchor.lat,
        anchor_lng=anchor.lon,
        near_place_ref=near_place_ref,
        max_km=max_km,
    )

    # Draw the destination as the lens's own anchor overlay (origin="lens").
    await _draw_lens_anchor(ctx, near_place_ref)

    # DistanceService is SQL (no fallible engine) — no RoutingError to catch.
    await _apply_lens(ctx)

    if max_km is not None:
        return (
            f"Filtered to {state.total_results} listings within {max_km} km "
            f"(straight-line) of {anchor.label}. The map is now coloured by "
            "distance (bold = closer, faded = farther)."
        )
    return (
        f"Coloured the map by straight-line distance to {anchor.label} (bold = "
        f"closer, faded = farther). All {state.total_results} listings are still "
        "shown."
    )


@toolset.tool
async def clear_lens(ctx: RunContext[ChatDeps]) -> str:
    """Remove the active map lens — recolour the map back to the default pins.

    Use when the user wants to drop the colouring ("remove the commute lens",
    "stop colouring by travel time/distance", "back to normal pins"). Recolour
    ONLY: the current listings are unchanged — if a cutoff had filtered the set,
    run a search to bring the hidden listings back. The user can also dismiss the
    lens directly in the UI (the × on the lens legend); either way it stays gone
    until a new `apply_*_lens`.
    """
    state = ctx.deps.state
    if state.active_lens is None:
        return "There's no lens active right now."
    _clear_lens(ctx)
    return (
        "Removed the lens — pins are back to default. The current listings are "
        "unchanged."
    )


@dataclass
class LensCapability(AbstractCapability[ChatDeps]):
    """Map-lens tools (`apply_travel_time_lens`, `apply_distance_lens`,
    `clear_lens`) bundled as a capability. Wrapped in `StateEmittingToolset` so
    lens mutations auto-emit a STATE_SNAPSHOT (see `state_emission.py`)."""

    def get_toolset(self) -> AgentToolset[ChatDeps] | None:
        return StateEmittingToolset(toolset)
