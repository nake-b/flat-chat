"""RoutingService — per-listing travel time from an anchor, car or transit.

Orchestrates two thin clients (`OsrmClient` car, `MotisClient` transit) over the
active result set. The anchor + mode + optional cutoff arrive as a
`TravelTimeLens` (already resolved to coordinates by the caller):

  - **car** → `OsrmClient.table` (one anchor → many listings in one matrix).
  - **transit** → `MotisClient.one_to_all` gives anchor→stop minutes for EVERY
    reachable stop; a listing isn't a stop, so we add the LAST mile here:
    listing time = min over nearby stops of `(anchor→stop) + walk(stop→listing)`.
    Only stops within `CAP_LAST_MILE_WALK_M` count; listings with no reachable
    stop in range are absent (rendered "no data" / dropped under a cutoff).

Returned values are **minutes** (rounded). Engine failures raise `RoutingError`
so the calling tool can degrade gracefully. Transit mode also stamps
`lens.schedule_stale` / `lens.schedule_as_of` from the loaded MOTIS feed window,
so the caller can surface the schedule's age; car mode leaves them untouched.

See `agent-compound-docs/decisions/travel-time-routing.md`.
"""

from __future__ import annotations

from flat_chat.listings import thresholds
from flat_chat.listings.context import Anchor, Marker
from flat_chat.listings.geo import equirect_distance_m
from flat_chat.listings.lenses import TravelTimeLens
from flat_chat.routing.errors import RoutingError
from flat_chat.routing.motis import FeedWindow, MotisClient, ReachableStop
from flat_chat.routing.osrm import OsrmClient

# Re-exported so existing `from flat_chat.routing.service import RoutingError`
# call sites (chat/, routing/__init__.py) keep working after the split.
__all__ = ["RoutingError", "RoutingService"]

# Degrees-per-metre approximations at Berlin's latitude for the bounding-box
# pre-filter (cheap reject before the equirectangular distance).
_LAT_DEG_PER_M = 1.0 / 111_000.0
_LON_DEG_PER_M = 1.0 / 67_000.0


class RoutingService:
    """Travel-time orchestrator over the OSRM (car) + MOTIS (transit) clients.
    Agent-only; one per request."""

    def __init__(self, osrm: OsrmClient, motis: MotisClient):
        self._osrm = osrm
        self._motis = motis

    async def feed_window(self) -> FeedWindow | None:
        """The (first, last) loaded transit-timetable dates, or None if unknown.
        Delegates to `MotisClient` so the tool + health endpoint share it."""
        return await self._motis.feed_window()

    async def resolve(
        self, markers: list[Marker], lens: TravelTimeLens
    ) -> dict[str, float]:
        """Return `{marker_id: minutes}` from the anchor to each marker.

        Unreachable / unrouted markers are simply absent from the dict. Raises
        `RoutingError` if the engine is unreachable or the response is malformed.
        Transit mode stamps `lens.schedule_stale` / `lens.schedule_as_of`."""
        # Markers always carry coordinates (search drops null-coordinate rows),
        # but guard anyway so a bad row can't desync a positional response.
        usable = [m for m in markers if m.lat is not None and m.lng is not None]
        if not usable:
            return {}

        anchor = Anchor(lens.anchor_label, lens.anchor_lat, lens.anchor_lng)
        if lens.mode == "car":
            return await self._osrm.table(anchor, usable)
        return await self._transit(usable, lens, anchor)

    async def _transit(
        self, markers: list[Marker], lens: TravelTimeLens, anchor: Anchor
    ) -> dict[str, float]:
        stops, departure = await self._motis.one_to_all(anchor, lens.max_minutes)
        lens.schedule_stale = departure.stale
        lens.schedule_as_of = departure.as_of
        if not stops:
            return {}
        return _last_mile(markers, stops)


def _last_mile(
    markers: list[Marker], stops: list[ReachableStop]
) -> dict[str, float]:
    """Each listing's transit time = min over stops within walking range of
    (anchor→stop minutes + walk minutes). A cheap lat/lon bounding-box pre-filter
    keeps this fast (a few k stops × tens of listings) before the distance calc.
    Walk speed + cap come from `thresholds` (single source of truth)."""
    cap_m = thresholds.CAP_LAST_MILE_WALK_M
    speed_m_per_min = thresholds.PEDESTRIAN_M_PER_S * 60.0
    lat_cap = cap_m * _LAT_DEG_PER_M
    lon_cap = cap_m * _LON_DEG_PER_M

    out: dict[str, float] = {}
    for m in markers:
        best: float | None = None
        for s in stops:
            if abs(s.lat - m.lat) > lat_cap or abs(s.lon - m.lng) > lon_cap:
                continue
            dist = equirect_distance_m(m.lat, m.lng, s.lat, s.lon)
            if dist > cap_m:
                continue
            total = s.minutes + dist / speed_m_per_min
            if best is None or total < best:
                best = total
        if best is not None:
            out[m.id] = round(best)
    return out
