"""ListingProximityCapability — single-listing point-to-point queries (deferred).

Two PURE-QUERY tools that answer a question about ONE apartment — the listing
the user has open (or a card by index) — against a single named destination:
  - `distance_to`      — straight-line km (PostGIS `ST_Distance`, no engine).
  - `travel_time_to`   — routed minutes (OSRM car / MOTIS transit).

These are the point-to-point counterpart of the whole-set map LENSES
(`chat/tools/lenses.py`): a lens colours/filters EVERY marker; these answer
"how far is *this one* from X" in prose and touch nothing — no result set, no
map, no active lens. That's why they reuse the same providers but build a
TRANSIENT lens that is never stored in `deps.state`: both `DistanceService` and
`RoutingService` expose `resolve(markers, lens) -> {id: value}`, so a
single-listing query is just `resolve([one_marker], transient_lens)`.

The capability is `defer_loading=True` — it's a late-session, single-listing
question many conversations never ask, so its tools + protocol prose stay OUT of
the cached prompt prefix until the model loads it on demand (via the
`load_capability` tool). See `agent-compound-docs/decisions/capability-landscape.md`.

Import SINK like `overlays.py`: imports only `state` + `emission` + the leaf
`listings`/`routing` types, none of the sibling tool modules — keeping the
`chat/tools/*` import graph acyclic.
"""

from __future__ import annotations

import logging
from dataclasses import KW_ONLY, dataclass

from pydantic_ai import FunctionToolset, ModelRetry, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AgentToolset

from flat_chat.chat.state import ChatDeps
from flat_chat.chat.tools.emission import StateEmittingToolset
from flat_chat.listings.context import Marker
from flat_chat.listings.lenses import DistanceLens, TravelTimeLens
from flat_chat.routing.errors import RoutingError

logger = logging.getLogger(__name__)

toolset: FunctionToolset[ChatDeps] = FunctionToolset()


_PROXIMITY_PROTOCOL = """\
<proximity_protocol>
POINT-TO-POINT tools for ONE apartment — a single origin → single destination
question. Distinct from the map LENSES (which colour/filter the WHOLE result
set): use these when the user asks about ONE specific flat, not all of them.
  - `distance_to(to_place_ref=…, from_index=…)` — straight-line (bird's-eye) km
    from one listing to a named place. For "how far is this from the Spree?".
  - `travel_time_to(to_place_ref=…, mode=…, from_index=…)` — routed minutes
    (transit or car) from one listing to a named place. For "how long to drive
    to the airport from this one?", "how far is #3 by U-Bahn from my office?".

Origin: by DEFAULT the listing the user has OPEN (the `<user_focus>` card). Pass
`from_index=N` (a 1-based card number) to measure from a different listing.
Destination: a `place_ref` from `locate_place` (resolve the name first).

These are PURE queries — they answer in prose and do NOT change the result set,
the map, or the active lens. Choosing between these and the lenses: "this one /
that flat / #3 / the one I opened" → these tools; "all of them / show me which
are within / colour the map" → `apply_travel_time_lens` / `apply_distance_lens`.
</proximity_protocol>

<proximity_phrase_map>
  - "how far is this from the Spree?"     → locate_place("Spree") →
                                            distance_to(to_place_ref=…)
  - "how far is #3 from TU Berlin?"        → locate_place("TU Berlin") →
                                            distance_to(to_place_ref=…,
                                            from_index=3)
  - "how long to drive to BER from this
    one?"                                  → locate_place("BER") →
                                            travel_time_to(to_place_ref=…,
                                            mode="car")
  - "how long by U-Bahn to Alexanderplatz
    from the flat I opened?"               → locate_place("Alexanderplatz") →
                                            travel_time_to(to_place_ref=…,
                                            mode="transit")
</proximity_phrase_map>
"""


@toolset.instructions
def proximity_protocol_instructions() -> str:
    """Toolset-scoped guidance for the point-to-point tools.

    Only enters the prompt AFTER the deferred capability is loaded (Pydantic AI
    appends a loaded capability's instructions like any other), so it costs
    nothing in the cached prefix of the many sessions that never ask.
    """
    return _PROXIMITY_PROTOCOL


def _resolve_origin(
    ctx: RunContext[ChatDeps], from_index: int | None
) -> tuple[Marker, str] | str:
    """Resolve the origin listing to a `(marker, label)` pair, or guidance prose.

    `from_index` (1-based) picks a specific card; otherwise the origin is the
    listing the user has OPEN (`state.active_id`). If that listing isn't in the
    current markers (a later refinement dropped it) but its detail blob is still
    open, fall back to the blob's own coordinates. Returns a plain string on any
    miss — the caller returns it verbatim so the agent can guide the user."""
    state = ctx.deps.state
    markers = state.result_markers
    if not markers:
        return "No active search results yet. Run search_apartments first."

    if from_index is not None:
        pos = from_index - 1
        if not (0 <= pos < len(markers)):
            return (
                f"There's no listing #{from_index} — the current result set has "
                f"{len(markers)} listing(s) (1–{len(markers)})."
            )
        return markers[pos], f"Listing #{from_index}"

    active_id = state.active_id
    if active_id is None:
        return (
            "No listing is open right now, so I don't know which apartment you "
            "mean. Open one first, or tell me which card by its number."
        )
    for m in markers:
        if m.id == active_id:
            return m, "This apartment"

    # active_id set but not among the current markers — fall back to the open
    # listing's own coordinates (kept on the detail blob) so the query still
    # works right after a refinement that dropped it from the set.
    detail = state.active_listing_detail
    if (
        detail is not None
        and detail.latitude is not None
        and detail.longitude is not None
    ):
        return (
            Marker(id=active_id, lat=detail.latitude, lng=detail.longitude),
            "This apartment",
        )
    return (
        "The listing you had open isn't in the current results anymore. Open one "
        "from the current list (or give me a card number) and I'll measure from it."
    )


@toolset.tool
async def distance_to(
    ctx: RunContext[ChatDeps],
    to_place_ref: str,
    from_index: int | None = None,
) -> str:
    """Straight-line distance from ONE listing to a named place (prose answer).

    A PURE query: it does NOT change the result set, the map, or the active lens
    — use it to answer "how far is this apartment from X?" for a single listing.
    Distance is measured to the place's EXACT shape (a river line, a campus
    polygon), not just its centre. This is geometry only ("how far"); for "how
    long" use `travel_time_to`.

    Args:
        to_place_ref: A `place_ref` from `locate_place` for the destination.
            NEVER invent this token — resolve the place name first.
        from_index: Optional 1-based card number to measure FROM. Omit to use
            the listing the user currently has open (`active_id`).
    """
    origin = _resolve_origin(ctx, from_index)
    if isinstance(origin, str):
        return origin
    marker, origin_label = origin

    anchor = await ctx.deps.place_service.anchor_point(to_place_ref)
    if anchor is None:
        raise ModelRetry(
            f"Could not resolve place_ref {to_place_ref!r}. Call locate_place "
            "first and pass one of the returned place_ref tokens."
        )

    # Reuse the distance-lens provider with a TRANSIENT lens (never stored in
    # state): it measures `marker` to the place's exact geometry via ST_Distance.
    lens = DistanceLens(
        anchor_label=anchor.label,
        anchor_lat=anchor.lat,
        anchor_lng=anchor.lon,
        near_place_ref=to_place_ref,
    )
    values = await ctx.deps.distance_service.resolve([marker], lens)
    metres = values.get(marker.id)
    if metres is None:
        return (
            f"I couldn't measure the distance from {origin_label.lower()} to "
            f"{anchor.label} (its coordinates are missing)."
        )
    return (
        f"{origin_label} is about {metres / 1000.0:.1f} km from {anchor.label} "
        "(straight-line distance)."
    )


@toolset.tool
async def travel_time_to(
    ctx: RunContext[ChatDeps],
    to_place_ref: str,
    mode: str = "transit",
    from_index: int | None = None,
) -> str:
    """Routed travel time from ONE listing to a named place (prose answer).

    A PURE query: it does NOT change the result set, the map, or the active lens
    — use it to answer "how long from this apartment to X?" for a single listing.
    This is routed time ("how long"); for straight-line distance ("how far") use
    `distance_to`.

    Args:
        to_place_ref: A `place_ref` from `locate_place` for the destination.
            NEVER invent this token — resolve the place name first.
        mode: "transit" (public transport, default) or "car" (driving).
        from_index: Optional 1-based card number to measure FROM. Omit to use
            the listing the user currently has open (`active_id`).
    """
    origin = _resolve_origin(ctx, from_index)
    if isinstance(origin, str):
        return origin
    marker, origin_label = origin

    anchor = await ctx.deps.place_service.anchor_point(to_place_ref)
    if anchor is None:
        raise ModelRetry(
            f"Could not resolve place_ref {to_place_ref!r}. Call locate_place "
            "first and pass one of the returned place_ref tokens."
        )

    is_car = mode == "car"  # anything but "car" falls back to transit
    lens_mode = "car" if is_car else "transit"
    how = "by car" if is_car else "by public transport"

    # Reuse the travel-time provider with a TRANSIENT lens (never stored). One
    # origin → one destination: for car a 1×1 OSRM matrix; for transit the MOTIS
    # one-to-all + last-mile, same as the lens but over a single marker.
    lens = TravelTimeLens(
        anchor_label=anchor.label,
        anchor_lat=anchor.lat,
        anchor_lng=anchor.lon,
        near_place_ref=to_place_ref,
        mode=lens_mode,
    )
    try:
        values = await ctx.deps.routing_service.resolve([marker], lens)
    except RoutingError as exc:
        # Routing is a fallible external dependency — answer gracefully rather
        # than failing the turn (mirrors the lens tool's degradation policy).
        logger.warning("travel_time_to failed: %s", exc)
        return (
            f"I couldn't reach the {lens_mode} routing service to compute the "
            f"travel time to {anchor.label}. Want me to try again in a moment?"
        )

    minutes = values.get(marker.id)
    if minutes is None:
        return (
            f"{origin_label} has no reachable route to {anchor.label} {how} "
            "within the routable window."
        )

    # Transit only: if the MOTIS feed has lapsed the routing layer clamped the
    # departure and flagged the schedule's age — surface it like the lens tool.
    stale_note = ""
    if lens.mode == "transit" and lens.schedule_stale and lens.schedule_as_of:
        stale_note = (
            f" (Transit times reflect the timetable as of {lens.schedule_as_of}.)"
        )
    return (
        f"{origin_label} is about {round(minutes)} min from {anchor.label} "
        f"{how}.{stale_note}"
    )


# The load-catalog routing hint the model sees for the deferred capability — it
# decides whether to load it from THIS text, so it must crisply distinguish the
# single-listing point-to-point job from the whole-set lens.
_PROXIMITY_DESCRIPTION = (
    "Measure straight-line distance or routed travel time (public transport or "
    "car) from ONE apartment — the listing the user has open, or a specific card "
    "by its index — to a named place. For single-listing questions like 'how far "
    "is this flat from FU Berlin?' or 'how long to drive to the airport from the "
    "one I opened?'. NOT for colouring the whole map by travel time/distance — "
    "that's the always-available travel-time / distance lens."
)


@dataclass
class ListingProximityCapability(AbstractCapability[ChatDeps]):
    """Single-listing distance / travel-time tools bundled as a DEFERRED capability.

    `defer_loading=True` keeps these tools + their protocol prose out of the
    cached prompt prefix until the model loads the capability on demand (the
    textbook late-session, rarely-asked case; the always-loaded set stays
    undeferred). Requires a stable `id` so message history can identify the
    capability across a load; `description` is the load-catalog routing hint.

    `get_toolset()` wraps the toolset in `StateEmittingToolset` like the other
    capabilities — harmless for pure-query tools (a no-op diff emits nothing),
    and keeps the wrapping uniform. See `capability-landscape.md`.
    """

    _: KW_ONLY
    id: str | None = "listing_proximity"
    defer_loading: bool = True
    description: str | None = _PROXIMITY_DESCRIPTION

    def get_toolset(self) -> AgentToolset[ChatDeps] | None:
        return StateEmittingToolset(toolset)
