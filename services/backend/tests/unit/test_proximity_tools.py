"""Unit tests for the single-listing proximity tools (`ListingProximityCapability`).

Stubs the routing / distance / place services (no engines, no DB). Covers origin
resolution (active listing, `from_index`, out-of-range, none-open, fallback to
the open listing's detail coords), the distance + travel-time happy paths (transit
& car), graceful routing degradation, the unreachable / missing-value branches,
the transit stale-schedule note, and the `place_ref` retry. Every case asserts the
PURE-QUERY contract: these tools never mutate `SessionState`.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic_ai import ModelRetry

from flat_chat.chat.session_state import SessionState
from flat_chat.chat.tools import (
    ListingProximityCapability,
    distance_to,
    travel_time_to,
)
from flat_chat.listings.context import Anchor, ListingCard, ListingDetail, Marker
from flat_chat.routing.errors import RoutingError
from flat_chat.search.schemas import SearchParams


class _StubDistance:
    """Distance provider: `{id: metres}`. No routing, so it never errors."""

    def __init__(self, metres_by_id):
        self._m = metres_by_id
        self.calls: list[list[str]] = []

    async def resolve(self, markers, lens):
        self.calls.append([m.id for m in markers])
        return dict(self._m)


class _StubRouting:
    def __init__(
        self, minutes_by_id, *, error=False, schedule_as_of=None, schedule_stale=False
    ):
        self._m = minutes_by_id
        self._error = error
        self._schedule_as_of = schedule_as_of
        self._schedule_stale = schedule_stale
        self.last_lens = None

    async def resolve(self, markers, lens):
        if self._error:
            raise RoutingError("engine down")
        # Mirror the real orchestrator: stamp the transit schedule freshness onto
        # the (transient) lens so the tool can surface a stale-feed note.
        lens.schedule_as_of = self._schedule_as_of
        lens.schedule_stale = self._schedule_stale
        self.last_lens = lens
        return dict(self._m)


class _StubPlace:
    def __init__(self, anchor=Anchor("TU Berlin", 52.5, 13.3)):
        self._anchor = anchor

    async def anchor_point(self, place_ref):
        return self._anchor


def _ctx(state, *, routing=None, distance=None, place=None):
    deps = SimpleNamespace(
        routing_service=routing,
        distance_service=distance,
        place_service=place or _StubPlace(),
        state=state,
    )
    return SimpleNamespace(deps=deps)


def _state(n: int, *, active_index: int | None = None) -> SessionState:
    s = SessionState()
    s.search_params = SearchParams()
    s.result_markers = [
        Marker(id=f"id-{i}", lat=52.5, lng=13.4, lens_value=1000.0 + i)
        for i in range(n)
    ]
    s.preview_cards = [ListingCard(id=f"id-{i}", district="X") for i in range(n)]
    s.total_results = n
    if active_index is not None:
        s.active_id = f"id-{active_index}"
    return s


def _assert_untouched(state: SessionState, *, markers_before, total_before) -> None:
    """The pure-query contract: no map / result-set / lens mutation."""
    assert [m.id for m in state.result_markers] == markers_before
    assert state.total_results == total_before
    assert state.active_lens is None
    assert state.marker_lens.key == "price_warm"
    assert state.map_overlays == []


# --- distance_to ------------------------------------------------------------


def test_distance_to_active_listing():
    state = _state(3, active_index=1)
    distance = _StubDistance({"id-1": 4200.0})
    ctx = _ctx(state, distance=distance)

    out = asyncio.run(distance_to(ctx, to_place_ref="place:x:1"))

    assert distance.calls == [["id-1"]]  # measured from the OPEN listing
    assert "4.2 km" in out
    assert "TU Berlin" in out
    assert "This apartment" in out
    _assert_untouched(state, markers_before=["id-0", "id-1", "id-2"], total_before=3)


def test_distance_to_from_index_overrides_active():
    state = _state(3, active_index=0)
    distance = _StubDistance({"id-2": 1500.0})
    ctx = _ctx(state, distance=distance)

    out = asyncio.run(distance_to(ctx, to_place_ref="place:x:1", from_index=3))

    assert distance.calls == [["id-2"]]  # #3 == id-2, not the active id-0
    assert "1.5 km" in out
    assert "Listing #3" in out
    _assert_untouched(state, markers_before=["id-0", "id-1", "id-2"], total_before=3)


def test_distance_to_from_index_out_of_range_is_guidance():
    state = _state(2, active_index=0)
    distance = _StubDistance({})
    ctx = _ctx(state, distance=distance)

    out = asyncio.run(distance_to(ctx, to_place_ref="place:x:1", from_index=9))

    assert "no listing #9" in out.lower()
    assert distance.calls == []  # never reached the provider
    _assert_untouched(state, markers_before=["id-0", "id-1"], total_before=2)


def test_distance_to_no_open_listing_is_guidance():
    state = _state(3)  # no active_id
    distance = _StubDistance({})
    ctx = _ctx(state, distance=distance)

    out = asyncio.run(distance_to(ctx, to_place_ref="place:x:1"))

    assert "open" in out.lower()
    assert distance.calls == []
    _assert_untouched(state, markers_before=["id-0", "id-1", "id-2"], total_before=3)


def test_distance_to_no_results_is_guidance():
    state = SessionState()  # no markers
    ctx = _ctx(state, distance=_StubDistance({}))
    out = asyncio.run(distance_to(ctx, to_place_ref="place:x:1"))
    assert "search" in out.lower()


def test_distance_to_missing_value_is_honest():
    state = _state(2, active_index=0)
    distance = _StubDistance({})  # provider returns nothing for the origin
    ctx = _ctx(state, distance=distance)

    out = asyncio.run(distance_to(ctx, to_place_ref="place:x:1"))

    assert "couldn't" in out.lower() or "missing" in out.lower()
    _assert_untouched(state, markers_before=["id-0", "id-1"], total_before=2)


def test_distance_to_unresolvable_place_ref_retries():
    state = _state(2, active_index=0)
    ctx = _ctx(state, distance=_StubDistance({}), place=_StubPlace(anchor=None))
    with pytest.raises(ModelRetry):
        asyncio.run(distance_to(ctx, to_place_ref="garbage"))


def test_distance_to_origin_falls_back_to_open_detail_coords():
    # active_id set but NOT in the current markers (a refinement dropped it);
    # the open listing's detail coords keep the query working.
    state = _state(2)
    state.active_id = "ghost"
    state.active_listing_detail = ListingDetail(
        id="ghost", latitude=52.6, longitude=13.5
    )
    distance = _StubDistance({"ghost": 800.0})
    ctx = _ctx(state, distance=distance)

    out = asyncio.run(distance_to(ctx, to_place_ref="place:x:1"))

    assert distance.calls == [["ghost"]]
    assert "0.8 km" in out
    _assert_untouched(state, markers_before=["id-0", "id-1"], total_before=2)


# --- travel_time_to ---------------------------------------------------------


def test_travel_time_to_transit():
    state = _state(3, active_index=1)
    routing = _StubRouting({"id-1": 18})
    ctx = _ctx(state, routing=routing)

    out = asyncio.run(travel_time_to(ctx, to_place_ref="place:x:1"))

    assert routing.last_lens.mode == "transit"
    assert "18 min" in out
    assert "public transport" in out
    assert "TU Berlin" in out
    _assert_untouched(state, markers_before=["id-0", "id-1", "id-2"], total_before=3)


def test_travel_time_to_car():
    state = _state(2, active_index=0)
    routing = _StubRouting({"id-0": 25})
    ctx = _ctx(state, routing=routing)

    out = asyncio.run(travel_time_to(ctx, to_place_ref="place:x:1", mode="car"))

    assert routing.last_lens.mode == "car"
    assert "25 min" in out
    assert "by car" in out
    _assert_untouched(state, markers_before=["id-0", "id-1"], total_before=2)


def test_travel_time_to_routing_failure_degrades_gracefully():
    state = _state(2, active_index=0)
    routing = _StubRouting({}, error=True)
    ctx = _ctx(state, routing=routing)

    out = asyncio.run(travel_time_to(ctx, to_place_ref="place:x:1"))

    assert "couldn't" in out.lower() or "could not" in out.lower()
    _assert_untouched(state, markers_before=["id-0", "id-1"], total_before=2)


def test_travel_time_to_unreachable_reports_no_route():
    state = _state(2, active_index=0)
    routing = _StubRouting({})  # origin absent → unreachable, no error
    ctx = _ctx(state, routing=routing)

    out = asyncio.run(travel_time_to(ctx, to_place_ref="place:x:1"))

    assert "no reachable" in out.lower()
    _assert_untouched(state, markers_before=["id-0", "id-1"], total_before=2)


def test_travel_time_to_surfaces_stale_transit_schedule():
    state = _state(1, active_index=0)
    routing = _StubRouting(
        {"id-0": 30}, schedule_as_of="2025-12-14", schedule_stale=True
    )
    ctx = _ctx(state, routing=routing)

    out = asyncio.run(travel_time_to(ctx, to_place_ref="place:x:1"))

    assert "2025-12-14" in out
    _assert_untouched(state, markers_before=["id-0"], total_before=1)


def test_travel_time_to_from_index_out_of_range_is_guidance():
    state = _state(2, active_index=0)
    routing = _StubRouting({})
    ctx = _ctx(state, routing=routing)

    out = asyncio.run(travel_time_to(ctx, to_place_ref="place:x:1", from_index=5))

    assert "no listing #5" in out.lower()
    assert routing.last_lens is None  # never reached the provider


def test_travel_time_to_unresolvable_place_ref_retries():
    state = _state(2, active_index=0)
    ctx = _ctx(state, routing=_StubRouting({}), place=_StubPlace(anchor=None))
    with pytest.raises(ModelRetry):
        asyncio.run(travel_time_to(ctx, to_place_ref="garbage"))


# --- Capability -------------------------------------------------------------


def test_capability_is_deferred_with_stable_id():
    cap = ListingProximityCapability()
    assert cap.defer_loading is True
    assert cap.id == "listing_proximity"
    assert cap.description  # non-empty load-catalog routing hint
