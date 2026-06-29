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


def test_motis_parses_positional_array_and_skips_unreachable(monkeypatch):
    _install(
        monkeypatch,
        lambda url, params: _FakeResp([{"duration": 900}, {}, {"duration": 61}]),
    )
    out = asyncio.run(_svc().resolve(_markers(3), ANCHOR))
    assert out == {"m0": 15, "m2": 1}  # 900s→15, {}→absent, 61s→1


def test_motis_uses_latlon_and_transit_mode(monkeypatch):
    client = _install(monkeypatch, lambda url, params: _FakeResp([{"duration": 600}]))
    asyncio.run(_svc().resolve(_markers(1), ANCHOR))
    params = client.calls[0][1]
    assert params["one"] == "52.512;13.327"  # lat;lon (opposite of OSRM)
    assert params["mode"] == "TRANSIT"
    assert params["arriveBy"] == "false"


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
