"""Unit tests for state-mutation contracts in `chat/tools/`.

The three agent tools each have a state contract that's deterministic but
fragile across refactors:

  - `search_apartments` — replaces the result set (markers + preview cards),
    clears every dependent field (active_id, active_listing_detail).
  - `open_listing` — resolves 1-based indices against `result_markers`, clears
    stale active_listing_detail unconditionally, repopulates it ONLY on
    single-index success. Beyond the hot preview it hydrates cards via
    `get_cards` for the prose.
  - `get_result_page` — does NOT mutate state; hydrates the page's cards on
    demand (page 1 shortcuts to the preview).

We mock `SearchService` / `ListingService` (plain async classes) and
construct a `RunContext`-shaped object via `SimpleNamespace`. The tools only
touch `ctx.deps.{search_service, listing_service, state}` so a fake context
is enough — no LLM, no DB, no Pydantic AI runtime.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from flat_chat.chat.session_state import SessionState
from flat_chat.chat.tools import (
    get_result_page,
    open_listing,
    search_apartments,
)
from flat_chat.listings.context import ListingCard, ListingDetail, Marker
from flat_chat.search.schemas import DistrictCount, NumericFacet, ResultFacets

# ---------------------------------------------------------------------------
# Mock services
# ---------------------------------------------------------------------------


class _MockSearchService:
    """Returns canned (markers, preview, total, facets) for any params."""

    def __init__(
        self,
        markers: list[Marker],
        preview: list[ListingCard],
        total: int,
        facets: ResultFacets | None = None,
    ):
        self.markers = markers
        self.preview = preview
        self.total = total
        self.facets = facets
        self.called_with = None

    async def search(self, params):
        self.called_with = params
        return list(self.markers), list(self.preview), self.total, self.facets


class _MockListingService:
    """Canned tier-3 detail (or None) + tier-2 cards keyed by id."""

    def __init__(
        self,
        detail: ListingDetail | None = None,
        cards: list[ListingCard] | None = None,
    ):
        self.detail = detail
        self._cards = {c.id: c for c in (cards or [])}
        self.detail_calls: list[str] = []
        self.cards_calls: list[list[str]] = []

    async def get_detail(self, listing_id):
        self.detail_calls.append(str(listing_id))
        return self.detail

    async def get_cards(self, ids):
        self.cards_calls.append(list(ids))
        return [self._cards[i] for i in ids if i in self._cards]


class _NullPlaceService:
    """overlay_geometry stub — returns no geometry (search overlay rebuild is
    exercised, but no DB)."""

    async def overlay_geometry(self, place_ref, *, origin="search"):
        return None


class _NullTransitOverlayService:
    async def route_geometry(self, line, *, origin="search"):
        return None


def _ctx(
    state: SessionState, *, search=None, listing=None, place=None, transit=None
) -> SimpleNamespace:
    """Build the minimum RunContext shape the tools actually access."""
    deps = SimpleNamespace(
        search_service=search,
        listing_service=listing,
        place_service=place or _NullPlaceService(),
        transit_overlay_service=transit or _NullTransitOverlayService(),
        state=state,
    )
    return SimpleNamespace(deps=deps)


def _marker(idx: int) -> Marker:
    return Marker(id=f"id-{idx}", lat=52.5, lng=13.4, lens_value=1000.0 + idx)


def _card(idx: int) -> ListingCard:
    return ListingCard(
        id=f"id-{idx}",
        title=f"Apt #{idx}",
        lat=52.5,
        lng=13.4,
        district="Kreuzberg",
        price_warm_eur=1000.0 + idx * 100,
        rooms=2.0,
        area_sqm=50.0,
    )


# ---------------------------------------------------------------------------
# search_apartments
# ---------------------------------------------------------------------------


def test_search_apartments_populates_state():
    # The tool now just mutates state + returns prose; the STATE_SNAPSHOT is
    # emitted structurally by StateEmittingToolset (see test_state_emission.py).
    markers = [_marker(1), _marker(2)]
    preview = [_card(1), _card(2)]
    facets = ResultFacets(
        price_warm_eur=NumericFacet(min=1100.0, median=1150.0, max=1200.0),
        districts=[DistrictCount(district="Kreuzberg", count=2)],
    )
    search = _MockSearchService(markers, preview, total=42, facets=facets)
    state = SessionState()
    ctx = _ctx(state, search=search)

    result = asyncio.run(search_apartments(ctx, rooms_min=2.0))

    assert isinstance(result, str)
    assert state.search_params is not None
    assert state.search_params.rooms_min == 2.0
    assert state.total_results == 42
    assert [m.id for m in state.result_markers] == ["id-1", "id-2"]
    assert [c.id for c in state.preview_cards] == ["id-1", "id-2"]
    # Whole-set facets are plumbed onto the state for the agent prompt.
    assert state.facets is not None
    assert state.facets.price_warm_eur.max == 1200.0
    # The tool itself returns plain prose; the STATE_SNAPSHOT is emitted by the
    # StateEmittingToolset wrapper, exercised end-to-end in test_state_emission.py.


def test_search_apartments_clears_active_detail_from_previous_search():
    # State from the previous search must not leak across.
    search = _MockSearchService([_marker(99)], [_card(99)], total=1)
    state = SessionState()
    state.active_id = "stale-id"
    state.active_listing_detail = ListingDetail(id="stale-id", title="Old one")

    asyncio.run(search_apartments(_ctx(state, search=search), rooms_min=2.0))

    assert state.active_id is None
    assert state.active_listing_detail is None


def test_search_apartments_translates_geo_kwargs_into_params():
    """Sanity-check that the geo-context kwargs make it into the params
    object SearchService sees. Catches bit-rot in the kwargs->params plumbing."""
    from flat_chat.search.geo_filters import KitaFilter, TransitFilter

    search = _MockSearchService([], [], total=0)
    state = SessionState()

    asyncio.run(
        search_apartments(
            _ctx(state, search=search),
            transit=TransitFilter(modes=["u_bahn"], distance="near"),
            kita=KitaFilter(distance="near"),
            near_place_ref="park:42",
            inside_ring=True,
            max_noise="quiet",
            density="sparse",
        )
    )

    params = search.called_with
    assert params is not None
    assert params.transit is not None
    assert params.transit.modes == ["u_bahn"]
    assert params.kita is not None
    assert params.kita.distance == "near"
    assert params.near_place_ref == "park:42"
    assert params.inside_ring is True
    assert params.max_noise == "quiet"
    assert params.density == "sparse"


# ---------------------------------------------------------------------------
# locate_place — pure lookup, no state mutation, no snapshot
# ---------------------------------------------------------------------------


class _MockPlaceService:
    """Returns canned candidates for whatever name it gets."""

    def __init__(self, candidates):
        self.candidates = candidates
        self.called_with: str | None = None

    async def locate(self, name):
        self.called_with = name
        return list(self.candidates)


def _ctx_place(state: SessionState, place) -> SimpleNamespace:
    deps = SimpleNamespace(place_service=place, state=state)
    return SimpleNamespace(deps=deps)


def test_locate_place_returns_candidates_and_does_not_mutate_state():
    from flat_chat.chat.tools import locate_place
    from flat_chat.search.places import PlaceCandidate

    candidate = PlaceCandidate(
        place_ref="park:7",
        kind="park",
        name="Tiergarten",
        description=None,
        lat=52.514,
        lon=13.35,
    )
    place = _MockPlaceService([candidate])
    state = SessionState()
    state.total_results = 5  # pre-existing result set must be untouched

    out = asyncio.run(locate_place(_ctx_place(state, place), place_name="Tiergarten"))

    # Pure lookup: plain string, NOT a ToolReturn (no StateSnapshotEvent).
    assert isinstance(out, str)
    assert "place_ref=park:7" in out
    assert "Tiergarten" in out
    assert place.called_with == "Tiergarten"
    # State is untouched — no snapshot, no result-set replacement.
    assert state.total_results == 5
    assert state.result_markers == []


def test_locate_place_no_match_returns_guidance():
    from flat_chat.chat.tools import locate_place

    place = _MockPlaceService([])
    out = asyncio.run(
        locate_place(_ctx_place(SessionState(), place), place_name="Nonexistent")
    )
    assert isinstance(out, str)
    assert "No place named" in out


# ---------------------------------------------------------------------------
# open_listing
# ---------------------------------------------------------------------------


def test_open_listing_empty_results_returns_message_and_no_mutation():
    state = SessionState()
    out = asyncio.run(open_listing(_ctx(state), indices=[1]))
    assert isinstance(out, str)
    assert "No active search results" in out
    assert state.active_id is None
    assert state.active_listing_detail is None


def test_open_listing_single_index_sets_active_and_loads_detail():
    state = SessionState()
    state.result_markers = [_marker(1), _marker(2)]
    state.preview_cards = [_card(1), _card(2)]
    state.total_results = 2

    detail = ListingDetail(id="id-1", title="Apt #1")
    listing = _MockListingService(detail=detail)

    result = asyncio.run(open_listing(_ctx(state, listing=listing), indices=[1]))

    assert isinstance(result, str)
    assert state.active_id == "id-1"
    assert state.active_listing_detail is detail
    # tier-3 detail fetched by resolved id; no card hydration (in preview).
    assert listing.detail_calls == ["id-1"]
    assert listing.cards_calls == []


def test_open_listing_multi_index_anchors_but_does_not_load_detail():
    state = SessionState()
    state.result_markers = [_marker(1), _marker(2)]
    state.preview_cards = [_card(1), _card(2)]
    state.total_results = 2
    state.active_listing_detail = ListingDetail(id="id-stale", title="Stale")

    listing = _MockListingService(detail=ListingDetail(id="id-1", title="Apt #1"))

    asyncio.run(open_listing(_ctx(state, listing=listing), indices=[1, 2]))

    # UI focus anchors to the first index.
    assert state.active_id == "id-1"
    # But the detail blob is cleared — multi-index = no tier-3 fetch.
    assert state.active_listing_detail is None
    assert listing.detail_calls == []


def test_open_listing_beyond_preview_hydrates_card_for_prose():
    # Index past the hot preview → the prose card hydrates via get_cards.
    state = SessionState()
    state.result_markers = [_marker(i) for i in range(1, 13)]  # 12 markers
    state.preview_cards = [_card(i) for i in range(1, 11)]  # preview = 10
    state.total_results = 12

    listing = _MockListingService(
        detail=ListingDetail(id="id-12", title="Apt #12"),
        cards=[_card(12)],
    )

    result = asyncio.run(open_listing(_ctx(state, listing=listing), indices=[12]))

    assert state.active_id == "id-12"
    assert listing.detail_calls == ["id-12"]
    # The prose card (index 12, beyond preview) was hydrated.
    assert listing.cards_calls == [["id-12"]]
    assert "Apt #12" in result


def test_open_listing_out_of_range_clears_detail_and_keeps_active_id():
    state = SessionState()
    state.result_markers = [_marker(1)]
    state.preview_cards = [_card(1)]
    state.total_results = 1
    state.active_id = "previous-id"
    state.active_listing_detail = ListingDetail(id="previous-id", title="Prev")

    listing = _MockListingService()

    result = asyncio.run(open_listing(_ctx(state, listing=listing), indices=[99]))

    assert state.active_listing_detail is None
    assert state.active_id == "previous-id"
    assert listing.detail_calls == []
    assert isinstance(result, str)
    assert "out of range" in result


# ---------------------------------------------------------------------------
# get_result_page
# ---------------------------------------------------------------------------


def test_get_result_page_empty_results():
    state = SessionState()
    out = asyncio.run(get_result_page(_ctx(state), page=1))
    assert isinstance(out, str)
    assert "No active search results" in out


def test_get_result_page_page_one_shortcuts_to_preview_no_db():
    state = SessionState()
    state.result_markers = [_marker(1), _marker(2)]
    state.preview_cards = [_card(1), _card(2)]
    state.total_results = 2
    state.search_params = __import__(
        "flat_chat.search.schemas", fromlist=["SearchParams"]
    ).SearchParams()

    listing = _MockListingService()
    before = state.model_dump()
    out = asyncio.run(
        get_result_page(_ctx(state, listing=listing), page=1, page_size=10)
    )
    after = state.model_dump()

    assert isinstance(out, str)
    assert "Page 1/1" in out
    # Page 1 served from preview — no hydration, no mutation.
    assert listing.cards_calls == []
    assert before == after


def test_get_result_page_beyond_preview_hydrates_slice():
    state = SessionState()
    state.result_markers = [_marker(i) for i in range(1, 16)]  # 15 markers
    state.preview_cards = [_card(i) for i in range(1, 11)]  # preview = 10
    state.total_results = 15

    listing = _MockListingService(cards=[_card(i) for i in range(11, 16)])
    out = asyncio.run(
        get_result_page(_ctx(state, listing=listing), page=2, page_size=10)
    )

    assert "Page 2/2 — listings 11–15 of 15" in out
    # Page 2 (positions 11-15) hydrated by marker id.
    assert listing.cards_calls == [["id-11", "id-12", "id-13", "id-14", "id-15"]]


def test_get_result_page_out_of_range():
    state = SessionState()
    state.result_markers = [_marker(1), _marker(2)]
    state.preview_cards = [_card(1), _card(2)]
    state.total_results = 2
    out = asyncio.run(get_result_page(_ctx(state), page=99, page_size=10))
    assert "out of range" in out


# ---------------------------------------------------------------------------
# Imports don't bit-rot
# ---------------------------------------------------------------------------


def test_tools_are_importable():
    assert callable(search_apartments)
    assert callable(open_listing)
    assert callable(get_result_page)


# ---------------------------------------------------------------------------
# Map overlays — show_on_map / clear_map_overlays / search auto-draw
# ---------------------------------------------------------------------------


from flat_chat.chat.tools import (  # noqa: E402
    clear_map_overlays,
    hide_on_map,
    show_on_map,
)
from flat_chat.listings.overlays import MapOverlay  # noqa: E402


class _StubPlaceOverlay:
    """overlay_geometry returns a fixed overlay (or None) — records origin."""

    def __init__(self, overlay: MapOverlay | None):
        self._overlay = overlay
        self.origins: list[str] = []

    async def overlay_geometry(self, place_ref, *, origin="search"):
        self.origins.append(origin)
        if self._overlay is None:
            return None
        # echo the requested origin so callers can assert it
        return self._overlay.model_copy(update={"origin": origin})


class _StubTransitOverlay:
    def __init__(self, overlay: MapOverlay | None):
        self._overlay = overlay
        self.origins: list[str] = []

    async def route_geometry(self, line, *, origin="search"):
        self.origins.append(origin)
        if self._overlay is None:
            return None
        return self._overlay.model_copy(update={"origin": origin})


def _place_overlay(ref: str = "park:7") -> MapOverlay:
    return MapOverlay(
        id=f"place:{ref}", kind="place", label="Tiergarten", geojson={"type": "Point"}
    )


def _line_overlay(line: str = "U7") -> MapOverlay:
    return MapOverlay(
        id=f"transit_line:{line}",
        kind="transit_line",
        label=line,
        geojson={"type": "LineString"},
    )


def test_show_on_map_requires_an_argument():
    state = SessionState()
    out = asyncio.run(show_on_map(_ctx(state)))
    assert "at least one" in out
    assert state.map_overlays == []


def test_show_on_map_pins_a_transit_line():
    state = SessionState()
    transit = _StubTransitOverlay(_line_overlay("U7"))
    ctx = _ctx(state, transit=transit)

    out = asyncio.run(show_on_map(ctx, transit_lines=["U7"]))

    assert "U7" in out
    assert [o.id for o in state.map_overlays] == ["transit_line:U7"]
    # show_on_map pins (survives the next search).
    assert state.map_overlays[0].origin == "pinned"
    assert transit.origins == ["pinned"]


def test_show_on_map_draws_multiple_lines_in_one_call():
    # Batch draw: one atomic call resolves + pins every line (no parallel-call
    # race on shared state). The stub echoes whatever line it's asked for.
    class _MultiTransit:
        def __init__(self):
            self.origins = []

        async def route_geometry(self, line, *, origin="search"):
            self.origins.append(origin)
            return _line_overlay(line).model_copy(update={"origin": origin})

    state = SessionState()
    ctx = _ctx(state, transit=_MultiTransit())
    out = asyncio.run(show_on_map(ctx, transit_lines=["U8", "U9"]))

    assert "U8" in out and "U9" in out
    assert [o.id for o in state.map_overlays] == [
        "transit_line:U8",
        "transit_line:U9",
    ]


def test_show_on_map_missing_geometry_reports_and_draws_nothing():
    state = SessionState()
    ctx = _ctx(state, transit=_StubTransitOverlay(None))
    out = asyncio.run(show_on_map(ctx, transit_lines=["X99"]))
    assert "Couldn't find" in out
    assert state.map_overlays == []


def test_hide_on_map_removes_by_label_and_id():
    state = SessionState()
    state.map_overlays = [_line_overlay("U8"), _line_overlay("U9"), _place_overlay()]
    # mix a label ("U8") and an id ("transit_line:U9") — both should match.
    out = asyncio.run(hide_on_map(_ctx(state), targets=["U8", "transit_line:U9"]))
    assert "U8" in out and "U9" in out
    assert [o.id for o in state.map_overlays] == ["place:park:7"]


def test_hide_on_map_not_drawn_is_a_no_op_message():
    state = SessionState()
    state.map_overlays = [_line_overlay("U8")]
    out = asyncio.run(hide_on_map(_ctx(state), targets=["S7"]))
    assert "Nothing matching" in out
    # untouched
    assert [o.id for o in state.map_overlays] == ["transit_line:U8"]


def test_hide_on_map_hints_when_overlay_is_an_active_search_filter():
    state = SessionState()
    u8 = _line_overlay("U8")
    u8.origin = "search"  # tied to a live filter
    state.map_overlays = [u8]
    out = asyncio.run(hide_on_map(_ctx(state), targets=["U8"]))
    assert "Removed U8" in out
    assert "drop that filter" in out
    assert state.map_overlays == []


def test_clear_map_overlays_empties_and_reports():
    state = SessionState()
    state.map_overlays = [_line_overlay("U7"), _place_overlay()]
    out = asyncio.run(clear_map_overlays(_ctx(state)))
    assert "Cleared 2" in out
    assert state.map_overlays == []


def test_clear_map_overlays_when_empty():
    state = SessionState()
    out = asyncio.run(clear_map_overlays(_ctx(state)))
    assert "No geometries" in out


def test_search_auto_draws_place_and_keeps_pinned():
    # A pinned line overlay must survive a search; a near_place_ref search adds
    # its place overlay as origin="search".
    state = SessionState()
    state.map_overlays = [_line_overlay("U7")]  # pinned-style, but origin defaults
    state.map_overlays[0].origin = "pinned"

    search = _MockSearchService([_marker(1)], [_card(1)], total=1)
    place = _StubPlaceOverlay(_place_overlay("park:7"))
    ctx = _ctx(state, search=search, place=place)

    asyncio.run(search_apartments(ctx, near_place_ref="park:7", radius_km=1.0))

    ids = {o.id: o.origin for o in state.map_overlays}
    assert ids == {"transit_line:U7": "pinned", "place:park:7": "search"}
    assert place.origins == ["search"]


def test_search_replaces_previous_search_overlays():
    # A prior search overlay is dropped when a new search runs without anchors.
    state = SessionState()
    state.map_overlays = [
        MapOverlay(
            id="place:park:1",
            kind="place",
            label="Old",
            geojson={"type": "Point"},
            origin="search",
        )
    ]
    search = _MockSearchService([_marker(1)], [_card(1)], total=1)
    asyncio.run(search_apartments(_ctx(state, search=search), rooms_min=1.0))

    # No anchors → no search overlays; the stale one is gone.
    assert state.map_overlays == []
