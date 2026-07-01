"""OsrmClient — car travel time from one anchor to many listings.

OSRM `/table` (verified against the running image, June 2026):
  `GET /table/v1/driving/{anchor};{m1};…?sources=0&annotations=duration`
returns `durations[0]` = seconds from the anchor (index 0) to every coordinate.
Coordinates are **lon,lat**. `null` = unroutable. The default `--max-table-size`
is 100, so destinations are chunked. Returned values are **minutes** (rounded);
`null`/unrouted markers are simply absent from the result.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx

from flat_chat.listings.context import Anchor, Marker
from flat_chat.routing.errors import RoutingError

# Destinations per OSRM request. Keeps the GET URL bounded (each coord is
# ~20 chars) and stays under OSRM's table-size limit even at the default 100 —
# 90 leaves headroom for the anchor + query string.
_CHUNK = 90

# Per-request network timeout (seconds). One matrix call over a city-sized graph
# is tens of milliseconds; this is a generous ceiling.
_TIMEOUT = 20.0


def _chunked(seq: list[Marker], size: int) -> Iterator[list[Marker]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


class OsrmClient:
    """Thin async client over the OSRM `/table` endpoint (car profile)."""

    def __init__(self, *, osrm_url: str, timeout: float = _TIMEOUT):
        self.osrm_url = osrm_url.rstrip("/")
        self.timeout = timeout

    async def table(self, anchor: Anchor, markers: list[Marker]) -> dict[str, float]:
        """`{marker_id: minutes}` by car from `anchor` to each marker.

        Unrouted markers are absent from the dict. Raises `RoutingError` if OSRM
        is unreachable or returns a non-`Ok` code / malformed matrix."""
        out: dict[str, float] = {}
        origin = f"{anchor.lon},{anchor.lat}"  # OSRM is lon,lat
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                for batch in _chunked(markers, _CHUNK):
                    coords = ";".join(f"{m.lng},{m.lat}" for m in batch)
                    url = (
                        f"{self.osrm_url}/table/v1/driving/{origin};{coords}"
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
