"""Unit tests for the routing layer — OSRM/MOTIS clients + RoutingService orchestrator.

No network: `httpx.AsyncClient` is monkeypatched with a fake whose `.get()`
returns canned engine payloads. Guards the things most likely to break silently:
the duration UNITS (engines return seconds; we expose minutes), the coord order
(OSRM lon,lat vs MOTIS lat,lon), the last-mile walk, the departure clamp, and the
feed-window read. The feed-window cache is now instance-level on `MotisClient`, so
each `_svc()` / fresh client starts clean — no module reset hook.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from flat_chat.listings.context import Marker
from flat_chat.listings.lenses import TravelTimeLens
from flat_chat.routing.errors import RoutingError
from flat_chat.routing.motis import (
    MotisClient,
    _commute_departure,
    _parse_metrics_window,
)
from flat_chat.routing.osrm import OsrmClient
from flat_chat.routing.service import RoutingService

_BERLIN = ZoneInfo("Europe/Berlin")

ANCHOR = TravelTimeLens(
    anchor_label="TU Berlin",
    anchor_lat=52.512,
    anchor_lng=13.327,
    mode="transit",
    max_minutes=None,
)

# A MOTIS /metrics body carrying the two nigiri timetable-window gauges. The
# timestamps are midnight-UTC unix seconds for 2026-06-26 (Fri) and
# 2026-07-01 (Wed).
_METRICS_BODY = (
    "# HELP nigiri_timetable_first_day_timestamp_seconds first day\n"
    "# TYPE nigiri_timetable_first_day_timestamp_seconds gauge\n"
    'nigiri_timetable_first_day_timestamp_seconds{tag="vbb"} 1782432000\n'
    'nigiri_timetable_last_day_timestamp_seconds{tag="vbb"} 1782864000\n'
)


def _markers(n: int) -> list[Marker]:
    return [
        Marker(id=f"m{i}", lat=52.5 + i / 1000, lng=13.4 + i / 1000) for i in range(n)
    ]


class _FakeResp:
    def __init__(self, payload=None, *, text=""):
        self._payload = payload
        self.text = text

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
    # osrm.py and motis.py both `import httpx` and call `httpx.AsyncClient(...)`,
    # so patching the module attribute covers both clients.
    client = _FakeClient(responder)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: client)
    return client


def _svc() -> RoutingService:
    return RoutingService(
        OsrmClient(osrm_url="http://osrm:5000"),
        MotisClient(motis_url="http://motis:8080"),
    )


def _one_to_all_call(client: _FakeClient) -> tuple[str, dict]:
    """The recorded one-to-all call (transit routing also GETs /metrics for the
    feed window first, so it's not guaranteed to be calls[0])."""
    for url, params in client.calls:
        if url.endswith("/api/v1/one-to-all"):
            return url, params  # type: ignore[return-value]
    raise AssertionError("no one-to-all call recorded")


def _motis_responder(payload):
    """Answer /metrics with the feed-window body and everything else with
    `payload` (the one-to-all response)."""

    def _r(url, params):
        if url.endswith("/metrics"):
            return _FakeResp(text=_METRICS_BODY)
        return _FakeResp(payload)

    return _r


def test_osrm_parses_seconds_to_minutes_and_skips_unrouted(monkeypatch):
    # durations[0] = [anchor→anchor, anchor→m0, anchor→m1, anchor→m2]
    _install(
        monkeypatch,
        lambda url, params: _FakeResp(
            {"code": "Ok", "durations": [[0, 120, 330, None]]}
        ),
    )
    lens = ANCHOR.model_copy(update={"mode": "car"})
    out = asyncio.run(_svc().resolve(_markers(3), lens))
    # 120s→2min, 330s→5.5→6min (round), None → absent.
    assert out == {"m0": 2, "m1": 6}


def test_osrm_builds_lonlat_sources0_url(monkeypatch):
    client = _install(
        monkeypatch,
        lambda url, params: _FakeResp({"code": "Ok", "durations": [[0, 60]]}),
    )
    lens = ANCHOR.model_copy(update={"mode": "car"})
    asyncio.run(_svc().resolve(_markers(1), lens))
    url = client.calls[0][0]
    assert "/table/v1/driving/13.327,52.512;" in url  # anchor lon,lat first
    assert "sources=0" in url
    assert "annotations=duration" in url


def test_osrm_raises_routing_error_on_bad_code(monkeypatch):
    _install(monkeypatch, lambda url, params: _FakeResp({"code": "NoTable"}))
    lens = ANCHOR.model_copy(update={"mode": "car"})
    with pytest.raises(RoutingError):
        asyncio.run(_svc().resolve(_markers(2), lens))


# MOTIS one-to-all returns transit minutes from the anchor to every reachable
# STOP; RoutingService adds the last-mile walk from the nearest in-range stop to
# each listing. These markers are spread so each sits ON one stop and far from
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
    _install(monkeypatch, _motis_responder(_ONE_TO_ALL))
    out = asyncio.run(_svc().resolve(_M_ONSTOP, ANCHOR))
    # m0/m1 sit on a stop → walk ≈ 0 → the stop's minutes; m2 has no stop within
    # the walk cap → absent.
    assert out == {"m0": 12, "m1": 24}


def test_motis_uses_one_to_all_latlon_and_transit(monkeypatch):
    client = _install(monkeypatch, _motis_responder(_ONE_TO_ALL))
    asyncio.run(_svc().resolve(_M_ONSTOP, ANCHOR))
    url, params = _one_to_all_call(client)
    assert url.endswith("/api/v1/one-to-all")
    assert params["one"] == "52.512,13.327"  # lat,lon comma (opposite of OSRM)
    assert params["transitModes"] == "TRANSIT"
    assert params["arriveBy"] == "false"
    # No cutoff → request the server's max-minutes cap.
    assert params["maxTravelTime"] == 90


def test_motis_clamps_max_travel_time_to_server_cap(monkeypatch):
    client = _install(monkeypatch, _motis_responder(_ONE_TO_ALL))
    over_cap = ANCHOR.model_copy(update={"max_minutes": 200})
    asyncio.run(_svc().resolve(_M_ONSTOP, over_cap))
    assert _one_to_all_call(client)[1]["maxTravelTime"] == 90  # clamped from 200
    client.calls.clear()
    under_cap = ANCHOR.model_copy(update={"max_minutes": 25})
    asyncio.run(_svc().resolve(_M_ONSTOP, under_cap))
    assert _one_to_all_call(client)[1]["maxTravelTime"] == 25  # passed through


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


# --- transit feed window: /metrics parse + fetch ----------------------------


def test_parse_metrics_window_reads_both_gauges():
    window = _parse_metrics_window(_METRICS_BODY)
    assert window == (date(2026, 6, 26), date(2026, 7, 1))


def test_parse_metrics_window_none_when_gauges_absent():
    assert _parse_metrics_window("some_other_metric 5\n# a comment\n") is None


def test_feed_window_hits_metrics_and_parses(monkeypatch):
    client = _install(monkeypatch, lambda url, params: _FakeResp(text=_METRICS_BODY))
    window = asyncio.run(MotisClient(motis_url="http://motis:8080").feed_window())
    assert window == (date(2026, 6, 26), date(2026, 7, 1))
    assert client.calls[0][0].endswith("/metrics")


def test_feed_window_none_and_uncached_on_unreachable(monkeypatch):
    # One client instance across both reads: a failure must NOT be cached, so a
    # later successful read is picked up immediately (instance-level cache).
    motis = MotisClient(motis_url="http://motis:8080")

    def _boom(url, params):
        raise httpx.ConnectError("refused")

    _install(monkeypatch, _boom)
    assert asyncio.run(motis.feed_window()) is None

    _install(monkeypatch, lambda url, params: _FakeResp(text=_METRICS_BODY))
    assert asyncio.run(motis.feed_window()) == (date(2026, 6, 26), date(2026, 7, 1))


# --- departure clamp: in-window / lapsed / future / no-window ---------------

_WINDOW = (date(2026, 6, 26), date(2026, 7, 1))


def test_commute_departure_in_window_not_stale():
    now = datetime(2026, 6, 29, 6, 0, tzinfo=_BERLIN)  # Mon 06:00
    iso, stale, as_of = _commute_departure(_WINDOW, now=now)
    assert iso.startswith("2026-06-29T08:00")
    assert stale is False
    assert as_of is None


def test_commute_departure_lapsed_clamps_inside_window_and_marks_stale():
    now = datetime(2026, 7, 20, 9, 0, tzinfo=_BERLIN)
    iso, stale, as_of = _commute_departure(_WINDOW, now=now)
    assert iso.startswith("2026-06-30T08:00")
    assert stale is True
    assert as_of == "2026-06-30"


def test_commute_departure_future_window_clamps_to_first_weekday_not_stale():
    now = datetime(2026, 6, 1, 9, 0, tzinfo=_BERLIN)
    iso, stale, as_of = _commute_departure(_WINDOW, now=now)
    assert iso.startswith("2026-06-26T08:00")
    assert stale is False
    assert as_of == "2026-06-26"


def test_commute_departure_no_window_falls_back_to_next_weekday():
    now = datetime(2026, 7, 20, 9, 0, tzinfo=_BERLIN)  # Monday
    iso, stale, as_of = _commute_departure(None, now=now)
    assert iso.startswith("2026-07-21T08:00")  # next day, Tue
    assert stale is False
    assert as_of is None
