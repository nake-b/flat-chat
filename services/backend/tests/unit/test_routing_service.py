"""Unit tests for RoutingService — engine URL/param building + response parsing.

No network: `httpx.AsyncClient` is monkeypatched with a fake whose `.get()`
returns canned engine payloads. Guards the two things most likely to break
silently: the duration UNITS (engines return seconds; we expose minutes) and
the positional alignment of the response to the marker batch.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from flat_chat.listings.context import Marker, TravelTimeFilter
from flat_chat.routing import service as routing_mod
from flat_chat.routing.service import RoutingError, RoutingService

ANCHOR = TravelTimeFilter(
    anchor_label="TU Berlin",
    anchor_lat=52.512,
    anchor_lng=13.327,
    mode="transit",
    max_minutes=None,
)


def _markers(n: int) -> list[Marker]:
    return [
        Marker(id=f"m{i}", lat=52.5 + i / 1000, lng=13.4 + i / 1000) for i in range(n)
    ]


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeClient:
    """Records calls and returns whatever `responder(url, params)` yields."""

    def __init__(self, responder):
        self._responder = responder
        self.calls: list[tuple[str | None, dict | None]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url=None, *, params=None):
        self.calls.append((url, params))
        return self._responder(url, params)


def _install(monkeypatch, responder) -> _FakeClient:
    client = _FakeClient(responder)
    monkeypatch.setattr(routing_mod.httpx, "AsyncClient", lambda *a, **k: client)
    return client


def _svc() -> RoutingService:
    return RoutingService(osrm_url="http://osrm:5000", motis_url="http://motis:8080")


def test_osrm_parses_seconds_to_minutes_and_skips_unrouted(monkeypatch):
    # durations[0] = [anchor→anchor, anchor→m0, anchor→m1, anchor→m2]
    _install(
        monkeypatch,
        lambda url, params: _FakeResp(
            {"code": "Ok", "durations": [[0, 120, 330, None]]}
        ),
    )
    filt = ANCHOR.model_copy(update={"mode": "car"})
    out = asyncio.run(_svc().resolve(_markers(3), filt))
    # 120s→2min, 330s→5.5→6min (round), None → absent.
    assert out == {"m0": 2, "m1": 6}


def test_osrm_builds_lonlat_sources0_url(monkeypatch):
    client = _install(
        monkeypatch,
        lambda url, params: _FakeResp({"code": "Ok", "durations": [[0, 60]]}),
    )
    filt = ANCHOR.model_copy(update={"mode": "car"})
    asyncio.run(_svc().resolve(_markers(1), filt))
    url = client.calls[0][0]
    assert "/table/v1/driving/13.327,52.512;" in url  # anchor lon,lat first
    assert "sources=0" in url
    assert "annotations=duration" in url


def test_osrm_raises_routing_error_on_bad_code(monkeypatch):
    _install(monkeypatch, lambda url, params: _FakeResp({"code": "NoTable"}))
    filt = ANCHOR.model_copy(update={"mode": "car"})
    with pytest.raises(RoutingError):
        asyncio.run(_svc().resolve(_markers(2), filt))


# MOTIS one-to-all returns transit minutes from the anchor to every reachable
# STOP; RoutingService adds the last-mile walk from the nearest in-range stop to
# each listing. These markers are spread so each sits ON one stop and >1 km from
# the others (no cross-walk), making the expected total deterministic.
_M_ONSTOP = [
    Marker(id="m0", lat=52.50, lng=13.40),
    Marker(id="m1", lat=52.52, lng=13.45),
    Marker(id="m2", lat=52.40, lng=13.30),  # far from every stop → no data
]
_ONE_TO_ALL = {
    "all": [
        {"place": {"lat": 52.50, "lon": 13.40}, "duration": 12},  # on m0
        {"place": {"lat": 52.52, "lon": 13.45}, "duration": 24},  # on m1
    ]
}


def test_motis_maps_stops_to_listings_via_last_mile_walk(monkeypatch):
    _install(monkeypatch, lambda url, params: _FakeResp(_ONE_TO_ALL))
    out = asyncio.run(_svc().resolve(_M_ONSTOP, ANCHOR))
    # m0/m1 sit on a stop → walk ≈ 0 → the stop's minutes; m2 has no stop within
    # the walk cap → absent.
    assert out == {"m0": 12, "m1": 24}


def test_motis_uses_one_to_all_latlon_and_transit(monkeypatch):
    client = _install(monkeypatch, lambda url, params: _FakeResp(_ONE_TO_ALL))
    asyncio.run(_svc().resolve(_M_ONSTOP, ANCHOR))
    url, params = client.calls[0]
    assert url.endswith("/api/v1/one-to-all")
    assert params["one"] == "52.512,13.327"  # lat,lon comma (opposite of OSRM)
    assert params["transitModes"] == "TRANSIT"
    assert params["arriveBy"] == "false"
    # No cutoff → request the server's max-minutes cap.
    assert params["maxTravelTime"] == 90


def test_motis_clamps_max_travel_time_to_server_cap(monkeypatch):
    client = _install(monkeypatch, lambda url, params: _FakeResp(_ONE_TO_ALL))
    over_cap = ANCHOR.model_copy(update={"max_minutes": 200})
    asyncio.run(_svc().resolve(_M_ONSTOP, over_cap))
    assert client.calls[0][1]["maxTravelTime"] == 90  # clamped from 200
    client.calls.clear()
    under_cap = ANCHOR.model_copy(update={"max_minutes": 25})
    asyncio.run(_svc().resolve(_M_ONSTOP, under_cap))
    assert client.calls[0][1]["maxTravelTime"] == 25  # passed through


def test_raises_routing_error_when_engine_unreachable(monkeypatch):
    def _boom(url, params):
        raise httpx.ConnectError("refused")

    _install(monkeypatch, _boom)
    with pytest.raises(RoutingError):
        asyncio.run(_svc().resolve(_markers(1), ANCHOR))


def test_empty_markers_short_circuits(monkeypatch):
    client = _install(
        monkeypatch, lambda url, params: _FakeResp({"code": "Ok", "durations": [[0]]})
    )
    out = asyncio.run(_svc().resolve([], ANCHOR))
    assert out == {}
    assert client.calls == []
