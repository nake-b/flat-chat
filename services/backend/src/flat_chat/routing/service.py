"""RoutingService — per-listing travel time from an anchor, car or transit.

Computes `{listing_id: minutes}` over the active result set against the OSRM
(car) and MOTIS (transit) engines. The anchor + mode + optional cutoff arrive
as a `TravelTimeFilter` (already resolved to coordinates by the caller).

Engine notes (verified against the running images, June 2026):
  - OSRM `GET /table/v1/driving/{anchor};{m1};…?sources=0&annotations=duration`
    returns `durations[0]` = seconds from the anchor (index 0) to every
    coordinate. Coordinates are **lon,lat**. `null` = unroutable. The default
    `--max-table-size` is 100, so we chunk destinations.
  - MOTIS (transit) has NO point-to-point matrix for TRANSIT — `one-to-many`
    is street-modes only (`mode=TRANSIT` → "not supported for one-to-many").
    Transit reachability is `GET /api/v1/one-to-all?one=lat,lon&transitModes=
    TRANSIT&maxTravelTime=<minutes>&time=…&arriveBy=false`, which returns
    `{"all": [{place:{lat,lon,stopId,…}, duration:<minutes>}, …]}` — the
    transit time from the anchor (incl. its first-mile walk) to EVERY reachable
    stop. `one` is **lat,lon** (comma; the opposite order from OSRM). NB
    `maxTravelTime` is in MINUTES and the server caps it (default 90 via
    `onetoall_max_travel_minutes`).

A listing isn't a stop, so we add the LAST mile ourselves: a listing's transit
time = min over nearby stops of `(anchor→stop minutes) + walk(stop→listing)`.
Only stops within `_WALK_CAP_M` of the listing count; listings with no
reachable stop in range are absent (rendered "no data" / dropped under a
cutoff). Returned values are **minutes** (rounded); callers compare against
`max_minutes` and write them onto `Marker.channel_value`.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from flat_chat.listings.context import Marker, TravelTimeFilter

logger = logging.getLogger(__name__)

_BERLIN_TZ = ZoneInfo("Europe/Berlin")


def _commute_departure() -> str:
    """Departure time for a transit commute query: the NEXT weekday 08:00 in
    Berlin local time, as ISO 8601.

    A "commute" lens means a typical workday trip, not literally now — querying
    at 02:00 on a Sunday would return night/weekend service and make most
    listings look unreachable. Pinning to the next weekday morning gives
    comparable, representative times whenever the user runs the search.
    """
    now = datetime.now(_BERLIN_TZ)
    dep = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if dep <= now:
        dep += timedelta(days=1)
    while dep.weekday() >= 5:  # Sat=5, Sun=6 → roll to Monday
        dep += timedelta(days=1)
    return dep.isoformat()

# Destinations per OSRM request. Keeps the GET URL bounded (each coord is
# ~20 chars) and stays under OSRM's table-size limit even if it's left at the
# default 100 — we run 90 to leave headroom for the anchor + query string.
_CHUNK = 90

# Per-request network timeout (seconds). One matrix / one-to-all call over a
# city-sized graph is tens of milliseconds; this is a generous ceiling.
_TIMEOUT = 20.0

# Transit last-mile model. one-to-all gives anchor→stop minutes; we add the
# walk from the stop to the listing at a steady pace, ignoring stops beyond a
# reasonable walk so a far-flung stop can't "rescue" an otherwise unreachable
# listing.
_WALK_SPEED_M_PER_MIN = 80.0  # ~4.8 km/h
_WALK_CAP_M = 1000.0  # only stops within ~12 min walk of a listing count
# one-to-all server cap on `maxTravelTime` (config `onetoall_max_travel_minutes`,
# default 90). Requesting more is a 400, so clamp to it.
_MOTIS_MAX_MINUTES = 90


class RoutingError(RuntimeError):
    """A routing engine was unreachable or returned an unusable response.

    Raised so the calling tool can surface a graceful "couldn't compute travel
    times" message instead of half-applying a lens."""


def _chunked(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres. Good enough for last-mile walk ranking
    at city scale (sub-metre error vs the geodesic over ~1 km)."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class RoutingService:
    """Travel-time over external engines. Agent-only; one per request."""

    def __init__(self, *, osrm_url: str, motis_url: str):
        self.osrm_url = osrm_url.rstrip("/")
        self.motis_url = motis_url.rstrip("/")

    async def resolve(
        self, markers: list[Marker], filt: TravelTimeFilter
    ) -> dict[str, float]:
        """Return `{marker_id: minutes}` from the anchor to each marker.

        Unreachable / unrouted markers are simply absent from the dict (the
        caller renders them as "no data" or drops them under a cutoff). Raises
        `RoutingError` if the engine is unreachable or the response is malformed.
        """
        # Markers always carry coordinates (search drops null-coordinate rows),
        # but guard anyway so a bad row can't desync the positional response.
        usable = [m for m in markers if m.lat is not None and m.lng is not None]
        if not usable:
            return {}

        if filt.mode == "car":
            return await self._osrm(usable, filt)
        return await self._motis(usable, filt)

    # -- OSRM (car) ---------------------------------------------------------

    async def _osrm(
        self, markers: list[Marker], filt: TravelTimeFilter
    ) -> dict[str, float]:
        out: dict[str, float] = {}
        anchor = f"{filt.anchor_lng},{filt.anchor_lat}"  # OSRM is lon,lat
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                for batch in _chunked(markers, _CHUNK):
                    coords = ";".join(f"{m.lng},{m.lat}" for m in batch)
                    url = (
                        f"{self.osrm_url}/table/v1/driving/{anchor};{coords}"
                        "?sources=0&annotations=duration"
                    )
                    resp = await client.get(url)
                    resp.raise_for_status()
                    data = resp.json()
                    if data.get("code") != "Ok":
                        raise RoutingError(f"OSRM: {data.get('code')}")
                    # durations[0] = [anchor→anchor, anchor→m0, anchor→m1, …]
                    row = (data.get("durations") or [[]])[0]
                    for m, secs in zip(batch, row[1:], strict=False):
                        if isinstance(secs, int | float):
                            out[m.id] = round(secs / 60)
        except httpx.HTTPError as exc:
            raise RoutingError(f"OSRM unreachable: {exc}") from exc
        return out

    # -- MOTIS (transit) ----------------------------------------------------

    async def _motis(
        self, markers: list[Marker], filt: TravelTimeFilter
    ) -> dict[str, float]:
        # one-to-all: transit minutes from the anchor to EVERY reachable stop.
        one = f"{filt.anchor_lat},{filt.anchor_lng}"  # one-to-all is lat,lon
        # A representative weekday-morning departure (not "now") so commute
        # times don't collapse to night/weekend service. The budget bounds the
        # isochrone — clamp to the server's hard cap.
        depart = _commute_departure()
        budget = int(filt.max_minutes or _MOTIS_MAX_MINUTES)
        max_minutes = max(1, min(budget, _MOTIS_MAX_MINUTES))
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{self.motis_url}/api/v1/one-to-all",
                    params={
                        "one": one,
                        "transitModes": "TRANSIT",
                        "maxTravelTime": max_minutes,
                        "arriveBy": "false",
                        "time": depart,
                        "maxMatchingDistance": 500,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise RoutingError(f"MOTIS unreachable: {exc}") from exc

        reachable = data.get("all") if isinstance(data, dict) else None
        if not isinstance(reachable, list):
            raise RoutingError("MOTIS: unexpected response shape")

        # Flatten to (lat, lon, anchor→stop minutes).
        stops: list[tuple[float, float, float]] = []
        for entry in reachable:
            if not isinstance(entry, dict):
                continue
            place = entry.get("place")
            dur = entry.get("duration")
            if not isinstance(place, dict) or not isinstance(dur, int | float):
                continue
            lat, lon = place.get("lat"), place.get("lon")
            if isinstance(lat, int | float) and isinstance(lon, int | float):
                stops.append((float(lat), float(lon), float(dur)))
        if not stops:
            return {}

        # Last mile: each listing's time = min over stops within walking range
        # of (anchor→stop minutes + walk minutes). A cheap lat/lon bounding-box
        # pre-filter keeps this fast (a few k stops × tens of listings) before
        # the haversine. The box is generous (the walk cap converts to ~0.009°
        # lat / ~0.015° lon at Berlin's latitude).
        out: dict[str, float] = {}
        lat_cap = _WALK_CAP_M / 111_000.0
        lon_cap = _WALK_CAP_M / 67_000.0
        for m in markers:
            best: float | None = None
            for slat, slon, sdur in stops:
                if abs(slat - m.lat) > lat_cap or abs(slon - m.lng) > lon_cap:
                    continue
                dist = _haversine_m(m.lat, m.lng, slat, slon)
                if dist > _WALK_CAP_M:
                    continue
                total = sdur + dist / _WALK_SPEED_M_PER_MIN
                if best is None or total < best:
                    best = total
            if best is not None:
                out[m.id] = round(best)
        return out
