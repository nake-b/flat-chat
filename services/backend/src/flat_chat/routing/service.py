"""RoutingService — per-listing travel time from an anchor, car or transit.

Computes `{listing_id: minutes}` over the active result set against the OSRM
(car) and MOTIS (transit) engines. The anchor + mode + optional cutoff arrive
as a `TravelTimeFilter` (already resolved to coordinates by the caller).

Engine notes (verified against the running images, June 2026):
  - OSRM `GET /table/v1/driving/{anchor};{m1};…?sources=0&annotations=duration`
    returns `durations[0]` = seconds from the anchor (index 0) to every
    coordinate. Coordinates are **lon,lat**. `null` = unroutable. The default
    `--max-table-size` is 100, so we chunk destinations.
  - MOTIS `GET /api/v1/one-to-many?one=lat;lon&many=lat;lon,…&mode=…&max=…&
    arriveBy=false` returns a positional array aligned to `many`; each entry is
    `{"duration": seconds}` or `{}` when unreachable. Coordinates are **lat;lon**
    (the opposite order from OSRM). Transit also takes a departure `time`.

Returned values are **minutes** (rounded); callers compare against
`max_minutes` and write them onto `Marker.channel_value`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from flat_chat.listings.context import Marker, TravelTimeFilter

logger = logging.getLogger(__name__)

# Destinations per engine request. Keeps the GET URL bounded (each coord is
# ~20 chars) and stays under OSRM's table-size limit even if it's left at the
# default 100 — we run 90 to leave headroom for the anchor + query string.
_CHUNK = 90

# Per-request network timeout (seconds). One matrix/one-to-many call over a
# city-sized graph is tens of milliseconds; this is a generous ceiling.
_TIMEOUT = 20.0


class RoutingError(RuntimeError):
    """A routing engine was unreachable or returned an unusable response.

    Raised so the calling tool can surface a graceful "couldn't compute travel
    times" message instead of half-applying a lens."""


def _chunked(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


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
        out: dict[str, float] = {}
        one = f"{filt.anchor_lat};{filt.anchor_lng}"  # MOTIS is lat;lon
        # Departure now; MOTIS imports a full year so any near-future time has
        # service. A generous max keeps far-but-reachable listings in.
        depart = datetime.now(UTC).replace(microsecond=0).isoformat()
        max_secs = (filt.max_minutes or 120) * 60
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                for batch in _chunked(markers, _CHUNK):
                    many = ",".join(f"{m.lat};{m.lng}" for m in batch)
                    resp = await client.get(
                        f"{self.motis_url}/api/v1/one-to-many",
                        params={
                            "one": one,
                            "many": many,
                            "mode": "TRANSIT",
                            "max": max_secs,
                            "maxMatchingDistance": 500,
                            "arriveBy": "false",
                            "time": depart,
                        },
                    )
                    resp.raise_for_status()
                    rows = resp.json()
                    if not isinstance(rows, list):
                        raise RoutingError("MOTIS: unexpected response shape")
                    # Positional, aligned to `many`; {} = unreachable.
                    for m, entry in zip(batch, rows, strict=False):
                        if not isinstance(entry, dict):
                            continue
                        secs = entry.get("duration")
                        if isinstance(secs, int | float):
                            out[m.id] = round(secs / 60)
        except httpx.HTTPError as exc:
            raise RoutingError(f"MOTIS unreachable: {exc}") from exc
        return out
