"""RoutingService — per-listing travel time from an anchor, car or transit.

Orchestrates two thin clients (`OsrmClient` car, `MotisClient` transit) over a
set of listings. The neutral core method is `travel_times(markers, anchor, mode,
max_minutes)` — it takes only what the routing needs (no lens):

  - **car** → `OsrmClient.table` (one anchor → many listings in one matrix).
  - **transit** → `MotisClient.one_to_all` gives anchor→stop minutes for EVERY
    reachable stop; a listing isn't a stop, so we add the LAST mile here:
    listing time = min over nearby stops of `(anchor→stop) + walk(stop→listing)`.
    Only stops within `CAP_LAST_MILE_WALK_M` count; listings with no reachable
    stop in range are absent (rendered "no data" / dropped under a cutoff).

`travel_times` returns a `TravelTimeResult` — `{marker_id: minutes}` (rounded)
plus the transit-schedule freshness (`schedule_stale` / `schedule_as_of`) as
DATA, not by mutating its argument. `resolve(markers, lens)` is a thin
`LensValueProvider` adapter over it: it unpacks the `TravelTimeLens`, calls
`travel_times`, and stamps the freshness back onto the lens (which the lens layer
reads afterwards). So the point-to-point proximity tools call `travel_times`
directly without ever constructing a lens.

Engine failures raise `RoutingError` so the calling tool can degrade gracefully.

See `agent-compound-docs/decisions/travel-time-routing.md`.
"""

from __future__ import annotations

from typing import NamedTuple

from flat_chat.listings import thresholds
from flat_chat.listings.context import Anchor, Marker
from flat_chat.listings.geo import equirect_distance_m
from flat_chat.listings.lenses import ActiveLens, TravelTimeLens
from flat_chat.routing.motis import FeedWindow, MotisClient, ReachableStop
from flat_chat.routing.osrm import OsrmClient

# Degrees-per-metre approximations at Berlin's latitude for the bounding-box
# pre-filter (cheap reject before the equirectangular distance).
_LAT_DEG_PER_M = 1.0 / 111_000.0
_LON_DEG_PER_M = 1.0 / 67_000.0


class TravelTimeResult(NamedTuple):
    """The result of a `travel_times` call: `{marker_id: minutes}` (rounded) plus
    the transit-schedule freshness carried as DATA. `schedule_stale` /
    `schedule_as_of` are set from the loaded MOTIS feed window on the transit path
    (so a caller can say "schedule as of <date>"); both stay defaulted for car
    mode. Returning freshness here — rather than mutating a lens — keeps the core
    method free of the lens type; `resolve` copies it onto the lens for the lens
    layer."""

    values: dict[str, float]
    schedule_stale: bool = False
    schedule_as_of: str | None = None


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

    async def travel_times(
        self,
        markers: list[Marker],
        anchor: Anchor,
        mode: str,
        max_minutes: int | None = None,
    ) -> TravelTimeResult:
        """Route each marker to `anchor` — the neutral core (no lens).

        Returns a `TravelTimeResult`: `{marker_id: minutes}` plus the transit
        schedule freshness. Unreachable / unrouted markers are simply absent from
        the values dict. Raises `RoutingError` if the engine is unreachable or the
        response is malformed. Car mode leaves the freshness defaulted."""
        # Markers always carry coordinates (search drops null-coordinate rows),
        # but guard anyway so a bad row can't desync a positional response.
        usable = [m for m in markers if m.lat is not None and m.lng is not None]
        if not usable:
            return TravelTimeResult({})

        if mode == "car":
            return TravelTimeResult(await self._osrm.table(anchor, usable))
        return await self._transit(usable, anchor, max_minutes)

    async def resolve(
        self, markers: list[Marker], lens: ActiveLens
    ) -> dict[str, float]:
        """`LensValueProvider` adapter over `travel_times` — `{marker_id: minutes}`.

        The lens layer only ever routes a `travel_time` lens here, so narrow to
        `TravelTimeLens` up front, unpack it into the core call, and stamp the
        returned schedule freshness back onto the lens (the lens layer reads
        `lens.schedule_stale` / `lens.schedule_as_of` afterwards to caption the
        legend)."""
        assert isinstance(lens, TravelTimeLens)
        anchor = Anchor(lens.anchor_label, lens.anchor_lat, lens.anchor_lng)
        result = await self.travel_times(markers, anchor, lens.mode, lens.max_minutes)
        lens.schedule_stale = result.schedule_stale
        lens.schedule_as_of = result.schedule_as_of
        return result.values

    async def _transit(
        self, markers: list[Marker], anchor: Anchor, max_minutes: int | None
    ) -> TravelTimeResult:
        stops, departure = await self._motis.one_to_all(anchor, max_minutes)
        values = _last_mile(markers, stops) if stops else {}
        return TravelTimeResult(values, departure.stale, departure.as_of)


def _last_mile(markers: list[Marker], stops: list[ReachableStop]) -> dict[str, float]:
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
