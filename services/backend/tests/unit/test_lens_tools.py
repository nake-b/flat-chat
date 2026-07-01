"""Unit tests for the lens tools + the shared generic `_apply_lens`.

Stubs the routing / distance / place services (no engines, no DB). Covers the
state contract for BOTH lenses — annotate vs. hard-filter, the lens descriptor,
the no-result-set guard, graceful degradation on a routing failure, the
transit-schedule staleness note — plus the distance lens (a different provider,
proving the abstraction is provider-agnostic) and the refinement re-apply hook.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from flat_chat.chat.lens_tools import (
    apply_distance_lens,
    apply_travel_time_lens,
    clear_lens,
)
from flat_chat.chat.session_state import SessionState
from flat_chat.chat.tools import search_apartments
from flat_chat.listings.context import Anchor, ListingCard, Marker
from flat_chat.listings.lenses import TravelTimeLens
from flat_chat.routing.errors import RoutingError
from flat_chat.search.schemas import SearchParams


class _StubRouting:
    def __init__(
        self, minutes_by_id, *, error=False, schedule_as_of=None, schedule_stale=False
    ):
        self._m = minutes_by_id
        self._error = error
        self._schedule_as_of = schedule_as_of
        self._schedule_stale = schedule_stale

    async def resolve(self, markers, lens):
        if self._error:
            raise RoutingError("engine down")
        # Mirror the real orchestrator: stamp the transit schedule's freshness
        # onto the lens so the tool can surface a stale-feed note.
        lens.schedule_as_of = self._schedule_as_of
        lens.schedule_stale = self._schedule_stale
        return dict(self._m)


class _StubDistance:
    """Distance provider: `{id: metres}`. No routing, so it never errors."""

    def __init__(self, metres_by_id):
        self._m = metres_by_id

    async def resolve(self, markers, lens):
        return dict(self._m)


class _StubPlace:
    def __init__(self, anchor=Anchor("TU Berlin", 52.5, 13.3)):
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


def _ctx(state, *, routing=None, distance=None, place=None, listing=None, search=None):
    # apply_*_lens re-derives the full result set from search_params before
    # applying (the leak fix). The default search stub hands back the state's
    # CURRENT full set, so _refresh_result_set is a no-op that models "the search
    # filters produce exactly these markers." Pass an explicit `search` to model
    # a refinement that returns a different set.
    if search is None:
        search = _StubSearch(
            list(state.result_markers),
            list(state.preview_cards),
            state.total_results,
            state.facets,
        )
    deps = SimpleNamespace(
        search_service=search,
        routing_service=routing,
        distance_service=distance,
        place_service=place or _StubPlace(),
        listing_service=listing or _StubListing(),
        transit_overlay_service=SimpleNamespace(),
        state=state,
    )
    return SimpleNamespace(deps=deps)


def _state(n: int) -> SessionState:
    s = SessionState()
    s.search_params = SearchParams()  # non-None so apply_*_lens can re-derive
    s.result_markers = [
        Marker(id=f"id-{i}", lat=52.5, lng=13.4, lens_value=1000.0 + i)
        for i in range(n)
    ]
    s.preview_cards = [ListingCard(id=f"id-{i}", district="X") for i in range(n)]
    s.total_results = n
    return s


# --- Travel-time lens -------------------------------------------------------


def test_hard_filter_drops_over_cutoff_and_sets_lens():
    state = _state(4)
    routing = _StubRouting({"id-0": 10, "id-1": 50, "id-2": 20})  # id-3 unreachable
    ctx = _ctx(state, routing=routing)

    asyncio.run(apply_travel_time_lens(ctx, near_place_ref="place:x:1", max_minutes=30))

    # id-1 (50>30) and id-3 (unreachable) dropped; id-0, id-2 kept.
    assert [m.id for m in state.result_markers] == ["id-0", "id-2"]
    assert [m.lens_value for m in state.result_markers] == [10, 20]
    assert state.total_results == 2
    assert [c.id for c in state.preview_cards] == ["id-0", "id-2"]
    assert state.marker_lens.key == "commute_min"
    assert "TU Berlin" in (state.marker_lens.label or "")
    assert state.active_lens is not None
    assert state.active_lens.kind == "travel_time"
    assert state.active_lens.max_minutes == 30


def test_annotate_only_keeps_all_and_colours():
    state = _state(3)
    routing = _StubRouting({"id-0": 12, "id-1": 34})  # id-2 unreachable → None kept
    ctx = _ctx(state, routing=routing)

    asyncio.run(apply_travel_time_lens(ctx, near_place_ref="place:x:1"))  # no max

    assert [m.id for m in state.result_markers] == ["id-0", "id-1", "id-2"]
    assert [m.lens_value for m in state.result_markers] == [12, 34, None]
    assert state.total_results == 3
    assert state.marker_lens.key == "commute_min"


def test_no_result_set_is_guarded():
    state = SessionState()  # no markers
    ctx = _ctx(state, routing=_StubRouting({}))
    out = asyncio.run(apply_travel_time_lens(ctx, near_place_ref="place:x:1"))
    assert "search" in out.lower()
    assert state.active_lens is None
    assert state.marker_lens.key == "price_warm"  # untouched default


def test_routing_failure_degrades_gracefully():
    state = _state(3)
    ctx = _ctx(state, routing=_StubRouting({}, error=True))
    out = asyncio.run(apply_travel_time_lens(ctx, near_place_ref="place:x:1"))
    assert state.active_lens is None
    assert state.marker_lens.key == "price_warm"
    assert state.total_results == 3
    assert "couldn't" in out.lower() or "could not" in out.lower()


def test_mode_defaults_to_transit():
    state = _state(1)
    ctx = _ctx(state, routing=_StubRouting({"id-0": 5}))
    asyncio.run(apply_travel_time_lens(ctx, near_place_ref="place:x:1"))
    assert state.active_lens.mode == "transit"


def test_stale_transit_schedule_is_surfaced():
    state = _state(2)
    routing = _StubRouting(
        {"id-0": 10, "id-1": 20}, schedule_as_of="2026-07-01", schedule_stale=True
    )
    ctx = _ctx(state, routing=routing)

    out = asyncio.run(apply_travel_time_lens(ctx, near_place_ref="place:x:1"))

    assert state.active_lens.schedule_stale is True
    assert state.active_lens.schedule_as_of == "2026-07-01"
    assert "2026-07-01" in out


def test_fresh_transit_schedule_has_no_note():
    state = _state(2)
    ctx = _ctx(state, routing=_StubRouting({"id-0": 10, "id-1": 20}))
    out = asyncio.run(apply_travel_time_lens(ctx, near_place_ref="place:x:1"))
    assert state.active_lens.schedule_stale is False
    assert state.active_lens.schedule_as_of is None
    assert "schedule" not in out.lower()


# --- Distance lens (different provider, same abstraction) --------------------


def test_distance_hard_filter_drops_over_cutoff_and_sets_lens():
    state = _state(3)
    # metres from the anchor; max_km=2 → cutoff 2000 m.
    distance = _StubDistance({"id-0": 500.0, "id-1": 3000.0, "id-2": 1500.0})
    ctx = _ctx(state, distance=distance)

    asyncio.run(apply_distance_lens(ctx, near_place_ref="place:x:1", max_km=2))

    assert [m.id for m in state.result_markers] == ["id-0", "id-2"]
    assert [m.lens_value for m in state.result_markers] == [500.0, 1500.0]
    assert state.total_results == 2
    assert state.marker_lens.key == "distance_m"
    assert state.active_lens.kind == "distance"
    assert state.active_lens.max_km == 2


def test_distance_annotate_only_keeps_all():
    state = _state(3)
    distance = _StubDistance({"id-0": 500.0, "id-1": 3000.0})  # id-2 no distance
    ctx = _ctx(state, distance=distance)

    asyncio.run(apply_distance_lens(ctx, near_place_ref="place:x:1"))  # no cutoff

    assert [m.id for m in state.result_markers] == ["id-0", "id-1", "id-2"]
    assert [m.lens_value for m in state.result_markers] == [500.0, 3000.0, None]
    assert state.total_results == 3
    assert state.marker_lens.key == "distance_m"


def test_distance_no_result_set_is_guarded():
    state = SessionState()
    ctx = _ctx(state, distance=_StubDistance({}))
    out = asyncio.run(apply_distance_lens(ctx, near_place_ref="place:x:1"))
    assert "search" in out.lower()
    assert state.active_lens is None
    assert state.marker_lens.key == "price_warm"


# --- clear_lens -------------------------------------------------------------


def test_clear_lens_resets_lens_and_keeps_results():
    state = _state(3)
    ctx = _ctx(state, routing=_StubRouting({"id-0": 10, "id-1": 20, "id-2": 25}))
    asyncio.run(apply_travel_time_lens(ctx, near_place_ref="place:x:1", max_minutes=30))
    assert state.marker_lens.key == "commute_min"
    kept = state.total_results

    out = asyncio.run(clear_lens(ctx))
    assert state.active_lens is None
    assert state.marker_lens.key == "price_warm"
    assert state.total_results == kept  # recolour-only
    assert "removed" in out.lower()


def test_clear_lens_noop_when_no_lens_active():
    state = _state(2)
    ctx = _ctx(state, routing=_StubRouting({}))
    out = asyncio.run(clear_lens(ctx))
    assert "no lens" in out.lower()
    assert state.marker_lens.key == "price_warm"
    assert state.active_lens is None


def test_cutoff_refills_preview_from_beyond_the_window():
    # 12 markers but only the top-10 have preview cards (the real PREVIEW_N
    # cap). A cutoff drops id-3 — one of those top-10 — promoting id-10 (beyond
    # the preview window) into the preview. It carries no card data, so it must
    # be hydrated by id via get_cards; the preview must stay a full 10-length
    # prefix of the survivors.
    state = _state(12)
    state.preview_cards = state.preview_cards[:10]  # mimic the PREVIEW_N cap
    minutes = {f"id-{i}": (99 if i == 3 else 10) for i in range(12)}
    listing = _StubListing(
        [ListingCard(id="id-10", district="X"), ListingCard(id="id-11", district="X")]
    )
    ctx = _ctx(state, routing=_StubRouting(minutes), listing=listing)

    asyncio.run(apply_travel_time_lens(ctx, near_place_ref="place:x:1", max_minutes=30))

    survivors = [f"id-{i}" for i in range(12) if i != 3]
    assert [m.id for m in state.result_markers] == survivors
    assert [c.id for c in state.preview_cards] == survivors[:10]
    assert state.total_results == 11


def test_refinement_search_degrades_when_routing_down():
    # A refinement search re-applies the active lens; if routing is down it must
    # NOT fail the whole search. The lens is dropped and the SQL result set the
    # search just produced stands (graceful degradation).
    state = _state(3)
    state.active_lens = TravelTimeLens(
        anchor_label="TU Berlin",
        anchor_lat=52.5,
        anchor_lng=13.3,
        mode="transit",
        max_minutes=30,
    )
    # marker_lens is computed from active_lens — no need (and not allowed) to set.
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
    assert state.active_lens is None
    assert state.marker_lens.key == "price_warm"
    assert [m.id for m in state.result_markers] == ["id-0", "id-1", "id-2"]


# --- R2 regressions: anchor-switch leak (#5) + overlay ownership (#6) --------


def _place_with_overlays():
    """A place stub whose overlay_geometry returns a real MapOverlay (id
    `place:<ref>`) so the lens-anchor overlay lifecycle can be asserted."""
    from flat_chat.listings.overlays import MapOverlay

    class _P(_StubPlace):
        async def overlay_geometry(self, place_ref, *, origin="search"):
            return MapOverlay(
                id=f"place:{place_ref}",
                kind="place",
                label=place_ref,
                geojson={},
                origin=origin,
            )

    return _P()


def test_switching_anchor_does_not_compound_filters():
    # The leak: apply lens A (cutoff), then lens B (cutoff). B must filter the
    # FULL search set (search ∩ B), NOT A's leftovers (A ∩ B).
    state = _state(4)  # id-0..3
    routing = _StubRouting({"id-0": 10, "id-1": 20})  # A ≤30 → keeps 0,1
    distance = _StubDistance({"id-0": 500.0, "id-2": 1500.0, "id-3": 1800.0})
    ctx = _ctx(state, routing=routing, distance=distance)

    asyncio.run(apply_travel_time_lens(ctx, near_place_ref="A", max_minutes=30))
    assert {m.id for m in state.result_markers} == {"id-0", "id-1"}

    # B ≤2 km. If leaked (operates on {0,1}) → {0}; if fixed (full) → {0,2,3}.
    asyncio.run(apply_distance_lens(ctx, near_place_ref="B", max_km=2))
    assert {m.id for m in state.result_markers} == {"id-0", "id-2", "id-3"}
    assert state.active_lens.kind == "distance"


def test_lens_draws_and_clears_its_own_anchor_overlay():
    state = _state(2)
    ctx = _ctx(
        state,
        routing=_StubRouting({"id-0": 5, "id-1": 6}),
        place=_place_with_overlays(),
    )

    asyncio.run(apply_travel_time_lens(ctx, near_place_ref="A"))
    lens_overlays = [o for o in state.map_overlays if o.origin == "lens"]
    assert [o.id for o in lens_overlays] == ["place:A"]

    asyncio.run(clear_lens(ctx))
    assert not any(o.origin == "lens" for o in state.map_overlays)


def test_switching_anchor_replaces_the_lens_overlay():
    state = _state(2)
    ctx = _ctx(
        state,
        routing=_StubRouting({"id-0": 5, "id-1": 6}),
        distance=_StubDistance({"id-0": 100.0, "id-1": 200.0}),
        place=_place_with_overlays(),
    )
    asyncio.run(apply_travel_time_lens(ctx, near_place_ref="A"))
    asyncio.run(apply_distance_lens(ctx, near_place_ref="B"))
    lens_overlays = [o for o in state.map_overlays if o.origin == "lens"]
    assert [o.id for o in lens_overlays] == ["place:B"]  # A replaced, not duplicated


def test_lens_borrows_user_pinned_overlay_and_leaves_it_on_clear():
    from flat_chat.listings.overlays import MapOverlay

    state = _state(2)
    state.map_overlays = [
        MapOverlay(id="place:A", kind="place", label="A", geojson={}, origin="pinned")
    ]
    ctx = _ctx(state, routing=_StubRouting({"id-0": 5}), place=_place_with_overlays())

    asyncio.run(apply_travel_time_lens(ctx, near_place_ref="A"))
    # The place was already pinned → the lens borrows it, claims no lens overlay.
    assert all(o.origin != "lens" for o in state.map_overlays)
    assert any(o.id == "place:A" and o.origin == "pinned" for o in state.map_overlays)

    asyncio.run(clear_lens(ctx))
    # Clearing the lens must NOT remove the user's pin.
    assert any(o.id == "place:A" and o.origin == "pinned" for o in state.map_overlays)


def test_travel_label_names_the_mode():
    state = _state(1)
    asyncio.run(
        apply_travel_time_lens(
            _ctx(state, routing=_StubRouting({"id-0": 5})),
            near_place_ref="A",
            mode="car",
        )
    )
    assert state.marker_lens.label == "Minutes by car to TU Berlin"

    state2 = _state(1)
    asyncio.run(
        apply_travel_time_lens(
            _ctx(state2, routing=_StubRouting({"id-0": 5})), near_place_ref="A"
        )
    )
    assert state2.marker_lens.label == "Minutes by public transport to TU Berlin"


def test_distance_label_is_capitalized():
    state = _state(1)
    asyncio.run(
        apply_distance_lens(
            _ctx(state, distance=_StubDistance({"id-0": 500.0})), near_place_ref="A"
        )
    )
    assert state.marker_lens.label == "Kilometres to TU Berlin"
