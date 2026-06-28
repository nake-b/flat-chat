"""Unit tests for state-mutation contracts in `chat/tools.py`.

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

from ag_ui.core import EventType
from pydantic_ai import ToolReturn

from flat_chat.chat.session_state import SessionState
from flat_chat.chat.tools import (
    get_result_page,
    open_listing,
    search_apartments,
)
from flat_chat.listings.context import ListingCard, ListingDetail, Marker

# ---------------------------------------------------------------------------
# Mock services
# ---------------------------------------------------------------------------


class _MockSearchService:
    """Returns canned (markers, preview, total) for whatever params it gets."""

    def __init__(self, markers: list[Marker], preview: list[ListingCard], total: int):
        self.markers = markers
        self.preview = preview
        self.total = total
        self.called_with = None

    async def search(self, params):
        self.called_with = params
        return list(self.markers), list(self.preview), self.total


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


def _ctx(state: SessionState, *, search=None, listing=None) -> SimpleNamespace:
    """Build the minimum RunContext shape the tools actually access."""
    deps = SimpleNamespace(
        search_service=search,
        listing_service=listing,
        state=state,
    )
    return SimpleNamespace(deps=deps)


def _marker(idx: int) -> Marker:
    return Marker(id=f"id-{idx}", lat=52.5, lng=13.4, price_warm_eur=1000.0 + idx)


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


def test_search_apartments_populates_state_and_emits_snapshot():
    markers = [_marker(1), _marker(2)]
    preview = [_card(1), _card(2)]
    search = _MockSearchService(markers, preview, total=42)
    state = SessionState()
    ctx = _ctx(state, search=search)

    result = asyncio.run(search_apartments(ctx, rooms_min=2.0))

    assert isinstance(result, ToolReturn)
    assert state.search_params is not None
    assert state.search_params.rooms_min == 2.0
    assert state.total_results == 42
    assert [m.id for m in state.result_markers] == ["id-1", "id-2"]
    assert [c.id for c in state.preview_cards] == ["id-1", "id-2"]
    # State snapshot accompanies the return value for the frontend.
    assert len(result.metadata) == 1
    assert result.metadata[0].type == EventType.STATE_SNAPSHOT


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

    assert isinstance(result, ToolReturn)
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
    assert "Apt #12" in str(result.return_value)


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
    assert isinstance(result, ToolReturn)
    assert "out of range" in str(result.return_value)


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
