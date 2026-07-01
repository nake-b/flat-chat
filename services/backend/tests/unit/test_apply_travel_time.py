"""Unit tests for the `apply_travel_time` tool + shared `_apply_travel_lens`.

Stubs the routing + place services (no engines, no DB). Covers the state
contract: annotate vs. hard-filter, the commute lens descriptor, the
no-result-set guard, and graceful degradation on a routing failure.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from flat_chat.chat.session_state import SessionState
from flat_chat.chat.tools import apply_travel_time, clear_lens, search_apartments
from flat_chat.listings.context import (
    ListingCard,
    Marker,
    MarkerLens,
    TravelTimeFilter,
)
from flat_chat.routing.service import RoutingError


class _StubRouting:
    def __init__(
        self, minutes_by_id, *, error=False, schedule_as_of=None, schedule_stale=False
    ):
        self._m = minutes_by_id
        self._error = error
        self._schedule_as_of = schedule_as_of
        self._schedule_stale = schedule_stale

    async def resolve(self, markers, filt):
        if self._error:
            raise RoutingError("engine down")
        # Mirror the real `_motis`: annotate the filter with the transit
        # schedule's freshness so the tool can surface a stale-feed note.
        filt.schedule_as_of = self._schedule_as_of
        filt.schedule_stale = self._schedule_stale
        return dict(self._m)


class _StubPlace:
    def __init__(self, anchor=("TU Berlin", 52.5, 13.3)):
        self._anchor = anchor

    async def anchor_point(self, place_ref):
        return self._anchor

    async def overlay_geometry(self, place_ref, *, origin="search"):
        return None  # geometry rebuild is out of scope here


class _StubListing:
    """Hydrates cards by id, in the requested order, skipping unknown ids —
    same contract as the real `ListingService.get_cards`."""

    def __init__(self, cards=None):
        self._cards = {c.id: c for c in (cards or [])}

    async def get_cards(self, ids):
        return [self._cards[i] for i in ids if i in self._cards]


class _StubSearch:
    def __init__(self, markers, preview, total, facets=None):
        self._ret = (markers, preview, total, facets)

    async def search(self, params):
        return self._ret


def _ctx(state, *, routing, place=None, listing=None, search=None):
    deps = SimpleNamespace(
        search_service=search,
        routing_service=routing,
        place_service=place or _StubPlace(),
        listing_service=listing or _StubListing(),
        state=state,
    )
    return SimpleNamespace(deps=deps)


def _state(n: int) -> SessionState:
    s = SessionState()
    s.result_markers = [
        Marker(id=f"id-{i}", lat=52.5, lng=13.4, lens_value=1000.0 + i)
        for i in range(n)
    ]
    s.preview_cards = [ListingCard(id=f"id-{i}", district="X") for i in range(n)]
    s.total_results = n
    return s


def test_hard_filter_drops_over_cutoff_and_sets_lens():
    state = _state(4)
    routing = _StubRouting({"id-0": 10, "id-1": 50, "id-2": 20})  # id-3 unreachable
    ctx = _ctx(state, routing=routing)

    asyncio.run(apply_travel_time(ctx, near_place_ref="place:x:1", max_minutes=30))

    # id-1 (50>30) and id-3 (unreachable) dropped; id-0, id-2 kept.
    assert [m.id for m in state.result_markers] == ["id-0", "id-2"]
    assert [m.lens_value for m in state.result_markers] == [10, 20]
    assert state.total_results == 2
    # preview stays a prefix-consistent subset of the survivors.
    assert [c.id for c in state.preview_cards] == ["id-0", "id-2"]
    assert state.marker_lens.key == "commute_min"
    assert "TU Berlin" in (state.marker_lens.label or "")
    assert state.travel_time_filter is not None
    assert state.travel_time_filter.max_minutes == 30


def test_annotate_only_keeps_all_and_colours():
    state = _state(3)
    routing = _StubRouting({"id-0": 12, "id-1": 34})  # id-2 unreachable → None kept
    ctx = _ctx(state, routing=routing)

    asyncio.run(apply_travel_time(ctx, near_place_ref="place:x:1"))  # no max

    assert [m.id for m in state.result_markers] == ["id-0", "id-1", "id-2"]
    assert [m.lens_value for m in state.result_markers] == [12, 34, None]
    assert state.total_results == 3
    assert state.marker_lens.key == "commute_min"


def test_no_result_set_is_guarded():
    state = SessionState()  # no markers
    ctx = _ctx(state, routing=_StubRouting({}))
    out = asyncio.run(apply_travel_time(ctx, near_place_ref="place:x:1"))
    assert "search" in out.lower()
    assert state.travel_time_filter is None
    assert state.marker_lens.key == "price_warm"  # untouched default


def test_routing_failure_degrades_gracefully():
    state = _state(3)
    ctx = _ctx(state, routing=_StubRouting({}, error=True))
    out = asyncio.run(apply_travel_time(ctx, near_place_ref="place:x:1"))
    # Lens cleared, lens reset, markers untouched, agent told.
    assert state.travel_time_filter is None
    assert state.marker_lens.key == "price_warm"
    assert state.total_results == 3
    assert "couldn't" in out.lower() or "could not" in out.lower()


def test_mode_defaults_to_transit():
    state = _state(1)
    routing = _StubRouting({"id-0": 5})
    ctx = _ctx(state, routing=routing)
    asyncio.run(apply_travel_time(ctx, near_place_ref="place:x:1"))
    assert state.travel_time_filter.mode == "transit"


def test_clear_lens_resets_lens_and_keeps_results():
    # Apply a lens, then clear it: filter + lens reset to default, but the
    # result set is left as-is (recolour-only — no restore of dropped listings).
    state = _state(3)
    ctx = _ctx(state, routing=_StubRouting({"id-0": 10, "id-1": 20, "id-2": 25}))
    asyncio.run(apply_travel_time(ctx, near_place_ref="place:x:1", max_minutes=30))
    assert state.marker_lens.key == "commute_min"
    kept = state.total_results

    out = asyncio.run(clear_lens(ctx))
    assert state.travel_time_filter is None
    assert state.marker_lens.key == "price_warm"
    assert state.total_results == kept  # recolour-only
    assert "removed" in out.lower()


def test_stale_transit_schedule_is_surfaced():
    # A lapsed feed → the filter carries schedule_as_of/stale, and the prose
    # tells the user which day's schedule the times reflect.
    state = _state(2)
    routing = _StubRouting(
        {"id-0": 10, "id-1": 20}, schedule_as_of="2026-07-01", schedule_stale=True
    )
    ctx = _ctx(state, routing=routing)

    out = asyncio.run(apply_travel_time(ctx, near_place_ref="place:x:1"))

    assert state.travel_time_filter is not None
    assert state.travel_time_filter.schedule_stale is True
    assert state.travel_time_filter.schedule_as_of == "2026-07-01"
    assert "2026-07-01" in out


def test_fresh_transit_schedule_has_no_note():
    state = _state(2)
    routing = _StubRouting({"id-0": 10, "id-1": 20})  # in-window default
    ctx = _ctx(state, routing=routing)

    out = asyncio.run(apply_travel_time(ctx, near_place_ref="place:x:1"))

    assert state.travel_time_filter.schedule_stale is False
    assert state.travel_time_filter.schedule_as_of is None
    assert "schedule" not in out.lower()


def test_clear_lens_noop_when_no_lens_active():
    state = _state(2)
    ctx = _ctx(state, routing=_StubRouting({}))
    out = asyncio.run(clear_lens(ctx))
    assert "no travel-time lens" in out.lower()
    assert state.marker_lens.key == "price_warm"
    assert state.travel_time_filter is None


def test_cutoff_refills_preview_from_beyond_the_window():
    # 12 markers but only the top-10 have preview cards (the real PREVIEW_N
    # cap). A cutoff drops id-3 — one of those top-10 — promoting id-10 (which
    # lives beyond the preview window) into the preview. It carries no card
    # data, so it must be hydrated by id via get_cards; the preview must stay a
    # full 10-length prefix of the survivors, not silently short.
    state = _state(12)
    state.preview_cards = state.preview_cards[:10]  # mimic the PREVIEW_N cap
    minutes = {f"id-{i}": (99 if i == 3 else 10) for i in range(12)}
    listing = _StubListing(
        [ListingCard(id="id-10", district="X"), ListingCard(id="id-11", district="X")]
    )
    ctx = _ctx(state, routing=_StubRouting(minutes), listing=listing)

    asyncio.run(apply_travel_time(ctx, near_place_ref="place:x:1", max_minutes=30))

    survivors = [f"id-{i}" for i in range(12) if i != 3]
    assert [m.id for m in state.result_markers] == survivors
    # Preview is the first 10 survivors, in order — incl. the hydrated id-10.
    assert [c.id for c in state.preview_cards] == survivors[:10]
    assert state.total_results == 11


def test_refinement_search_degrades_when_routing_down():
    # A refinement search re-applies the active lens; if routing is down it must
    # NOT fail the whole search. The lens is dropped and the SQL result set the
    # search just produced stands (graceful degradation, same as the tool).
    state = _state(3)
    state.travel_time_filter = TravelTimeFilter(
        anchor_label="TU Berlin",
        anchor_lat=52.5,
        anchor_lng=13.3,
        mode="transit",
        max_minutes=30,
    )
    state.marker_lens = MarkerLens(key="commute_min", label="min to TU Berlin")
    fresh = [
        Marker(id=f"id-{i}", lat=52.5, lng=13.4, lens_value=900.0 + i) for i in range(3)
    ]
    preview = [ListingCard(id=f"id-{i}", district="X") for i in range(3)]
    ctx = _ctx(
        state,
        routing=_StubRouting({}, error=True),
        search=_StubSearch(fresh, preview, 3),
    )

    out = asyncio.run(search_apartments(ctx, query="kreuzberg"))

    assert isinstance(out, str)  # did not raise
    assert state.travel_time_filter is None
    assert state.marker_lens.key == "price_warm"
    assert [m.id for m in state.result_markers] == ["id-0", "id-1", "id-2"]
