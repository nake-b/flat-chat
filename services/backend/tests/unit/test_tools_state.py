"""Unit tests for state-mutation contracts in `chat/tools.py`.

The three agent tools each have a state contract that's deterministic but
fragile across refactors:

  - `search_apartments` — replaces the result set, clears every dependent
    field (active_id, active_listing_detail), drops cards without coords.
  - `open_listing` — clears stale active_listing_detail unconditionally,
    repopulates it ONLY on single-index success. The June 2026 regression
    that motivated this file: multi-index calls leaving the prior single-
    index detail attached, causing the frontend to render neighbourhood
    data for a card no longer in focus.
  - `get_result_page` — pure read. Must NOT mutate state.

We mock `SearchService` / `ListingService` (plain async classes) and
construct a `RunContext`-shaped object via `SimpleNamespace`. The tools
only touch `ctx.deps.{search_service, listing_service, state}` so a fake
context is enough — no LLM, no DB, no Pydantic AI runtime.
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
from flat_chat.listings.context import ListingDetail, UiApartment


# ---------------------------------------------------------------------------
# Mock services
# ---------------------------------------------------------------------------


class _MockSearchService:
    """Returns canned (cards, total) for whatever SearchParams it gets."""

    def __init__(self, cards: list[UiApartment], total: int):
        self.cards = cards
        self.total = total
        self.called_with = None

    async def search(self, params):
        self.called_with = params
        return list(self.cards), self.total


class _MockListingService:
    """Returns a canned ListingDetail (or None) for any id."""

    def __init__(self, detail: ListingDetail | None):
        self.detail = detail
        self.calls: list[str] = []

    async def get(self, listing_id):
        self.calls.append(str(listing_id))
        return self.detail


def _ctx(state: SessionState, *, search=None, listing=None) -> SimpleNamespace:
    """Build the minimum RunContext shape the tools actually access."""
    deps = SimpleNamespace(
        search_service=search,
        listing_service=listing,
        state=state,
    )
    return SimpleNamespace(deps=deps)


def _apt(idx: int, *, lat: float | None = 52.5, lng: float | None = 13.4) -> UiApartment:
    return UiApartment(
        id=f"id-{idx}",
        title=f"Apt #{idx}",
        lat=lat,
        lng=lng,
        district="Kreuzberg",
    )


# ---------------------------------------------------------------------------
# search_apartments
# ---------------------------------------------------------------------------


def test_search_apartments_populates_state_and_emits_snapshot():
    cards = [_apt(1), _apt(2)]
    search = _MockSearchService(cards, total=42)
    state = SessionState()
    ctx = _ctx(state, search=search)

    result = asyncio.run(search_apartments(ctx, rooms_min=2.0))

    assert isinstance(result, ToolReturn)
    # SearchParams reconstructed from kwargs lives on state.
    assert state.search_params is not None
    assert state.search_params.rooms_min == 2.0
    assert state.total_results == 42
    assert [a.id for a in state.results] == ["id-1", "id-2"]
    # State snapshot accompanies the return value for the frontend.
    assert len(result.metadata) == 1
    assert result.metadata[0].type == EventType.STATE_SNAPSHOT


def test_search_apartments_drops_cards_without_coordinates():
    # The contract: chat prose, card strip, and map markers must agree.
    # SearchService returns three cards; only the two with coords survive.
    cards = [
        _apt(1),
        _apt(2, lat=None, lng=None),
        _apt(3, lat=52.5, lng=None),  # half-set still drops
    ]
    search = _MockSearchService(cards, total=3)
    state = SessionState()

    asyncio.run(search_apartments(_ctx(state, search=search), rooms_min=2.0))

    assert [a.id for a in state.results] == ["id-1"]


def test_search_apartments_clears_active_detail_from_previous_search():
    # Regression: state from the previous search must not leak across.
    # Without the reset, frontend would render detail for a card no longer
    # in the result set.
    cards = [_apt(99)]
    search = _MockSearchService(cards, total=1)
    state = SessionState()
    state.active_id = "stale-id"
    state.active_listing_detail = ListingDetail(id="stale-id", title="Old one")

    asyncio.run(search_apartments(_ctx(state, search=search), rooms_min=2.0))

    assert state.active_id is None
    assert state.active_listing_detail is None


# ---------------------------------------------------------------------------
# open_listing
# ---------------------------------------------------------------------------


def test_open_listing_empty_results_returns_message_and_no_mutation():
    state = SessionState()
    out = asyncio.run(open_listing(_ctx(state), indices=[1]))
    assert isinstance(out, str)
    assert "No active search results" in out
    # Nothing touched.
    assert state.active_id is None
    assert state.active_listing_detail is None


def test_open_listing_single_index_sets_active_and_loads_detail():
    state = SessionState()
    state.results = [_apt(1), _apt(2)]
    state.total_results = 2

    detail = ListingDetail(id="id-1", title="Apt #1")
    listing = _MockListingService(detail)

    result = asyncio.run(open_listing(_ctx(state, listing=listing), indices=[1]))

    assert isinstance(result, ToolReturn)
    assert state.active_id == "id-1"
    # Single-index path populates the detail.
    assert state.active_listing_detail is detail
    # ListingService called with the resolved id (not the index).
    assert listing.calls == ["id-1"]


def test_open_listing_multi_index_anchors_but_does_not_load_detail():
    # The June 2026 regression: multi-index calls used to leave the prior
    # single-index detail attached. The fix: clear unconditionally on
    # entry, only repopulate on single-index success.
    state = SessionState()
    state.results = [_apt(1), _apt(2)]
    state.active_id = None
    state.active_listing_detail = ListingDetail(id="id-stale", title="Stale")

    listing = _MockListingService(ListingDetail(id="id-1", title="Apt #1"))

    asyncio.run(open_listing(_ctx(state, listing=listing), indices=[1, 2]))

    # UI focus anchors to the first index.
    assert state.active_id == "id-1"
    # But the detail blob is cleared — multi-index = no tier-3 fetch.
    assert state.active_listing_detail is None
    # And ListingService was NOT called.
    assert listing.calls == []


def test_open_listing_single_index_with_no_detail_keeps_active_id():
    # ListingService returns None (e.g. row deleted between search and
    # open). active_id still advances; detail stays None.
    state = SessionState()
    state.results = [_apt(1)]

    listing = _MockListingService(None)

    asyncio.run(open_listing(_ctx(state, listing=listing), indices=[1]))

    assert state.active_id == "id-1"
    assert state.active_listing_detail is None
    assert listing.calls == ["id-1"]


def test_open_listing_out_of_range_clears_detail_and_does_not_touch_active_id():
    state = SessionState()
    state.results = [_apt(1)]
    state.active_id = "previous-id"
    state.active_listing_detail = ListingDetail(id="previous-id", title="Prev")

    listing = _MockListingService(None)

    result = asyncio.run(open_listing(_ctx(state, listing=listing), indices=[99]))

    # The "clear on entry" rule wipes the stale detail even for an
    # out-of-range index. active_id stays at its previous value because
    # the tool only advances it when the index resolves.
    assert state.active_listing_detail is None
    assert state.active_id == "previous-id"
    # ListingService was NOT called — the index didn't resolve.
    assert listing.calls == []
    # Return value reports the out-of-range index via the standard prose.
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


def test_get_result_page_is_a_pure_read():
    # The docstring promises this tool does NOT mutate state. Verify by
    # taking a snapshot of every mutable field before and after.
    from flat_chat.search.schemas import SearchParams

    state = SessionState()
    state.results = [_apt(1), _apt(2)]
    state.total_results = 2
    state.search_params = SearchParams()
    state.active_id = "id-1"
    state.active_listing_detail = ListingDetail(id="id-1", title="Apt #1")

    before = state.model_dump()
    out = asyncio.run(get_result_page(_ctx(state), page=1, page_size=10))
    after = state.model_dump()

    assert isinstance(out, str)
    assert "Page 1/1" in out
    assert before == after


# ---------------------------------------------------------------------------
# Smoke: SearchParams round-trip via search_apartments
# ---------------------------------------------------------------------------


def test_search_apartments_translates_geo_kwargs_into_params():
    """Sanity-check that the geo-context kwargs make it into the params
    object that SearchService sees. Catches bit-rot in the kwargs->params
    plumbing (e.g. a field renamed in SearchParams but not in tools.py)."""
    from flat_chat.search.geo_filters import MssFilter, TransitFilter

    search = _MockSearchService([], total=0)
    state = SessionState()

    asyncio.run(
        search_apartments(
            _ctx(state, search=search),
            transit=TransitFilter(modes=["u_bahn"], distance="near"),
            mss=MssFilter(status_min="affluent"),
            max_noise="quiet",
            density="sparse",
        )
    )

    params = search.called_with
    assert params is not None
    assert params.transit is not None
    assert params.transit.modes == ["u_bahn"]
    assert params.mss is not None
    assert params.mss.status_min == "affluent"
    assert params.max_noise == "quiet"
    assert params.density == "sparse"


# Pydantic AI raises ValidationError if a tool is called with disallowed
# arg shapes through the agent runtime. We're calling functions directly
# so the runtime is bypassed — but make sure the imports themselves don't
# bit-rot (would surface immediately at module import otherwise).
def test_tools_are_importable():
    assert callable(search_apartments)
    assert callable(open_listing)
    assert callable(get_result_page)
