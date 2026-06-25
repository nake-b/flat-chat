"""Unit tests for `chat/llm_context.py`.

Every byte the LLM sees about result data and per-turn state flows through
this module. Prose changes here propagate directly into the LLM's tool-call
behaviour and prompt cache, so each test pins a specific shape.

Snapshot-style: hand-written expected strings. Auto-update is deliberately
not used — a diff has to be reviewed every time prose shifts.

No DB, no LLM.
"""

from __future__ import annotations

from flat_chat.chat.llm_context import (
    LlmResultSetView,
    build_dynamic_state_prompt,
    format_listing_detail_prose,
    format_navigation_footer,
)
from flat_chat.chat.session_state import SessionState
from flat_chat.listings.context import (
    DensityProfile,
    GreeneryProfile,
    ListingDetail,
    MssProfile,
    NearestHospital,
    NearestPark,
    NearestPlayground,
    NearestSchool,
    NearestTransitStop,
    NearestWater,
    NoiseProfile,
    SchoolCatchmentInfo,
    ListingCard,
)
from flat_chat.search.schemas import SearchParams


# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------


def _apt(idx: int, **overrides) -> ListingCard:
    """Build a ListingCard with the fields the formatters actually read."""
    defaults = dict(
        id=f"id-{idx}",
        title=f"Apt #{idx}",
        price_warm_eur=1000.0 + idx * 100,
        price_cold_eur=800.0 + idx * 100,
        rooms=2.0,
        area_sqm=50.0,
        district="Kreuzberg",
        lat=52.5,
        lng=13.4,
    )
    defaults.update(overrides)
    return ListingCard(**defaults)


def _state_with_results(n: int, *, total: int | None = None) -> SessionState:
    state = SessionState()
    state.search_params = SearchParams(rooms_min=2.0)
    state.results = [_apt(i + 1) for i in range(n)]
    state.total_results = total if total is not None else n
    return state


# ---------------------------------------------------------------------------
# LlmResultSetView.total / shown
# ---------------------------------------------------------------------------


def test_total_uses_total_results_when_larger_than_loaded():
    # search hit the LIMIT — total_results > len(results)
    state = _state_with_results(5, total=487)
    view = LlmResultSetView(state)
    assert view.total == 487
    assert view.shown == 5


def test_total_falls_back_to_len_when_total_results_is_zero():
    # SessionState defaults total_results=0, but results may be populated
    # via a path that didn't set it. `max(...)` is the guard.
    state = _state_with_results(3, total=0)
    view = LlmResultSetView(state)
    assert view.total == 3


# ---------------------------------------------------------------------------
# order_label
# ---------------------------------------------------------------------------


def test_order_label_no_params_is_recent():
    view = LlmResultSetView(SessionState())
    assert view.order_label() == "most recent first"


def test_order_label_relevance_with_query():
    state = _state_with_results(1)
    state.search_params = SearchParams(query="balcony", sort_by="relevance")
    assert LlmResultSetView(state).order_label() == "sorted by relevance to your query"


def test_order_label_relevance_without_query_falls_back_to_recent():
    # The "never lie" promise — relevance with no query degrades to recency.
    state = _state_with_results(1)
    state.search_params = SearchParams(sort_by="relevance")
    assert LlmResultSetView(state).order_label() == "most recent first"


def test_order_label_price():
    state = _state_with_results(1)
    state.search_params = SearchParams(sort_by="price")
    assert LlmResultSetView(state).order_label() == "sorted by lowest warm rent"


def test_order_label_area():
    state = _state_with_results(1)
    state.search_params = SearchParams(sort_by="area")
    assert LlmResultSetView(state).order_label() == "sorted by largest area"


def test_order_label_recent():
    state = _state_with_results(1)
    state.search_params = SearchParams(sort_by="recent")
    assert LlmResultSetView(state).order_label() == "most recent first"


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


def test_summary_zero_results_returns_no_match_prose():
    state = SessionState()
    state.search_params = SearchParams(rooms_min=2.0)
    out = LlmResultSetView(state).summary()
    assert "No apartments found" in out
    assert "Try broadening" in out


def test_summary_three_results_header_and_cards_and_footer():
    state = _state_with_results(3)
    out = LlmResultSetView(state).summary()
    lines = out.splitlines()
    # Header counts both total and order.
    assert lines[0] == "Found 3 listings, most recent first."
    assert lines[1] == "Showing 1–3:"
    # Each card line starts with "  N. " — verifying the prose builder.
    assert lines[2].startswith("  1. Apt #1 | €1100 | 2 rooms | 50m² | Kreuzberg")
    assert lines[3].startswith("  2. Apt #2")
    assert lines[4].startswith("  3. Apt #3")
    # Footer attached.
    assert "All loaded results shown above." in out
    assert "open_listing(indices=[N])" in out
    assert "search_apartments(...)" in out


def test_summary_caps_at_top_n_and_shows_remaining_in_footer():
    state = _state_with_results(12)
    out = LlmResultSetView(state).summary(top_n=5)
    assert "Showing 1–5:" in out
    assert "7 more loaded" in out
    assert "get_result_page(page=N)" in out


# ---------------------------------------------------------------------------
# page (CSV format)
# ---------------------------------------------------------------------------


def test_page_no_results():
    out = LlmResultSetView(SessionState()).page(1)
    assert out == "No results to page through. Run a search first."


def test_page_out_of_range_reports_total_pages():
    state = _state_with_results(3)
    out = LlmResultSetView(state).page(99, page_size=10)
    assert "Page 99 is out of range" in out
    assert "3 loaded results" in out
    assert "1 pages of 10" in out


def test_page_renders_csv_with_header_and_indices():
    state = _state_with_results(3)
    out = LlmResultSetView(state).page(1, page_size=10)
    assert "Page 1/1 — listings 1–3 of 3 loaded (3 total)" in out
    assert "```csv" in out
    assert "#,title,warm €,rooms,m²,district" in out
    # Each row begins with its 1-based index.
    assert "1,Apt #1,1100,2,50,Kreuzberg" in out
    assert "2,Apt #2,1200,2,50,Kreuzberg" in out
    assert "3,Apt #3,1300,2,50,Kreuzberg" in out


def test_page_escapes_csv_commas_and_quotes_in_title():
    state = SessionState()
    state.results = [_apt(1, title='2-Zi, "WBS only"')]
    state.total_results = 1
    state.search_params = SearchParams()
    out = LlmResultSetView(state).page(1)
    # CSV escaping: surround with quotes, double the inner ".
    assert '"2-Zi, ""WBS only"""' in out


def test_page_truncates_last_page():
    # 12 results, page_size=5 → page 3 holds positions 11..12.
    state = _state_with_results(12)
    out = LlmResultSetView(state).page(3, page_size=5)
    assert "Page 3/3 — listings 11–12 of 12 loaded" in out
    assert "11,Apt #11" in out
    assert "12,Apt #12" in out
    # Position 13 must not appear.
    assert "13,Apt" not in out


# ---------------------------------------------------------------------------
# detail
# ---------------------------------------------------------------------------


def test_detail_no_results():
    out = LlmResultSetView(SessionState()).detail([1])
    assert out == "No results to show details for. Run a search first."


def test_detail_out_of_range_index_reports_inline():
    state = _state_with_results(2)
    out = LlmResultSetView(state).detail([99])
    assert "#99: out of range (results are 1–2)." in out


def test_detail_multi_index_preserves_order():
    state = _state_with_results(3)
    out = LlmResultSetView(state).detail([3, 1])
    # The "#3" chunk must appear before "#1" — order follows the input list.
    pos_3 = out.index("--- Listing #3 ---")
    pos_1 = out.index("--- Listing #1 ---")
    assert pos_3 < pos_1


# ---------------------------------------------------------------------------
# format_navigation_footer
# ---------------------------------------------------------------------------


def test_navigation_footer_empty_results():
    # No results loaded — footer must still tell the LLM how to refine.
    out = format_navigation_footer(LlmResultSetView(SessionState()), shown_end=0)
    assert "search_apartments(...)" in out
    # Don't suggest pagination when there's nothing to paginate.
    assert "get_result_page" not in out
    assert "open_listing" not in out


def test_navigation_footer_all_loaded_shown():
    state = _state_with_results(3)
    out = format_navigation_footer(LlmResultSetView(state), shown_end=3)
    assert "All loaded results shown above." in out
    # No "more loaded" prose when nothing is left.
    assert "more loaded" not in out
    assert "open_listing(indices=[N])" in out


def test_navigation_footer_remaining_pages():
    state = _state_with_results(12)
    out = format_navigation_footer(LlmResultSetView(state), shown_end=5)
    assert "7 more loaded" in out
    assert "get_result_page(page=N)" in out


# ---------------------------------------------------------------------------
# format_listing_detail_prose
# ---------------------------------------------------------------------------


def _detail_full() -> ListingDetail:
    """A ListingDetail with every section populated, for the full-prose test."""
    return ListingDetail(
        id="abc",
        title="Sunny 2-room",
        price_warm_eur=1500.0,
        rooms=2.0,
        area_sqm=55.0,
        district="Kreuzberg",
        address="Manteuffelstr. 1",
        nearest_transit_stops=[
            NearestTransitStop(
                stop_id="900100001",
                name="U Kottbusser Tor",
                modes=["u_bahn"],
                lines=["U1", "U8"],
                distance_m=200,
                walk_minutes=3,
            )
        ],
        school_catchment=SchoolCatchmentInfo(school_name="GS Lenau"),
        nearest_schools=[
            NearestSchool(name="GS Lenau", school_type="Grundschule", distance_m=300),
        ],
        nearest_parks=[NearestPark(name="Görlitzer Park", distance_m=400)],
        nearest_playground=NearestPlayground(name="Mariannenplatz", distance_m=250),
        nearest_hospitals=[
            NearestHospital(name="Urban-Krankenhaus", tier="plan_hospital", distance_m=900)
        ],
        nearest_water=NearestWater(name="Landwehrkanal", water_kind="canal", distance_m=500),
        noise=NoiseProfile(label="lively", total_lden=60.0),
        greenery=GreeneryProfile(label="leafy", green_m2_within_300m=6000.0),
        density=DensityProfile(label="dense", persons_per_hectare=200.0),
        mss=MssProfile(status="mixed", dynamics="improving"),
        disabled_parking_count=3,
    )


def test_format_listing_detail_prose_full_listing_has_every_section():
    out = format_listing_detail_prose(idx=1, detail=_detail_full())

    # Header.
    assert "--- Listing #1 — full detail ---" in out
    # Identity / money block joined with " | ".
    assert "Sunny 2-room | €1500 warm | 2 rooms | 55 m² | Kreuzberg | Manteuffelstr. 1" in out
    # Transit section.
    assert "Nearby transit:" in out
    assert "  - U Kottbusser Tor — U1, U8 (200m, 3min walk)" in out
    # School catchment + schools.
    assert "Primary school catchment: GS Lenau" in out
    assert "Nearby schools:" in out
    assert "  - GS Lenau (Grundschule) — 300m" in out
    # Parks / playground.
    assert "Nearby parks:" in out
    assert "  - Görlitzer Park — 400m" in out
    assert "Nearest playground: Mariannenplatz — 250m" in out
    # Hospitals.
    assert "Hospitals nearby:" in out
    assert "  - Urban-Krankenhaus (plan_hospital) — 900m" in out
    # Water.
    assert "Nearest water: Landwehrkanal — 500m" in out
    # Character.
    assert (
        "Neighbourhood character: street noise: lively, greenery: leafy, "
        "density: dense, Sozialmonitoring: mixed · improving"
    ) in out
    # Disabled parking.
    assert "Disabled parking nearby: 3 spots within 300m" in out


def test_format_listing_detail_prose_minimal_listing_omits_empty_sections():
    minimal = ListingDetail(id="x", title="Bare")
    out = format_listing_detail_prose(idx=7, detail=minimal)
    # Only the header + identity block survive.
    assert "--- Listing #7 — full detail ---" in out
    assert "Bare" in out
    # Empty `nearest_*` lists must NOT emit headings.
    assert "Nearby transit:" not in out
    assert "Nearby schools:" not in out
    assert "Nearby parks:" not in out
    assert "Hospitals nearby:" not in out
    assert "Nearest playground:" not in out
    assert "Nearest water:" not in out
    # No character block when all profile fields are None.
    assert "Neighbourhood character:" not in out
    # No disabled-parking line when count is 0.
    assert "Disabled parking nearby:" not in out


# ---------------------------------------------------------------------------
# build_dynamic_state_prompt — the XML state snapshot per turn
# ---------------------------------------------------------------------------


def test_dynamic_prompt_empty_state_shows_no_search_yet():
    out = build_dynamic_state_prompt(SessionState())
    assert "<current_state>" in out
    assert "<total>0</total>" in out
    assert "No search has run yet in this conversation." in out
    # No user_focus block without an active_id.
    assert "<user_focus>" not in out


def test_dynamic_prompt_active_search_no_focus_omits_focus_block():
    state = _state_with_results(3, total=487)
    out = build_dynamic_state_prompt(state)
    assert "<current_state>" in out
    assert "<total>487</total>" in out
    assert "<loaded>3</loaded>" in out
    assert "<order>most recent first</order>" in out
    # filters JSON should mention the rooms_min the fixture set, but NOT
    # sort_by (the doc says order is surfaced separately).
    assert '"rooms_min": 2.0' in out
    assert "sort_by" not in out
    # No focus block when active_id is None.
    assert "<user_focus>" not in out


def test_dynamic_prompt_with_active_listing_emits_focus_block():
    state = _state_with_results(3)
    state.active_id = "id-2"
    state.active_listing_detail = ListingDetail(id="id-2", title="Active one")
    out = build_dynamic_state_prompt(state)
    assert "<user_focus>" in out
    # The "don't reopen" guidance uses the resolved 1-based index (2).
    assert "The user is viewing listing #2 in the detail panel." in out
    assert "Do NOT call open_listing for index 2" in out
    # The active-listing prose is nested inside an <active_listing> block.
    assert "<active_listing>" in out
    assert "Active one" in out


def test_dynamic_prompt_active_id_not_in_results_omits_focus_block():
    # active_id pointing at a card that's no longer in results (e.g. after
    # a re-search). Index resolution returns None → no focus block.
    state = _state_with_results(2)
    state.active_id = "id-stale"
    state.active_listing_detail = ListingDetail(id="id-stale", title="Stale")
    out = build_dynamic_state_prompt(state)
    assert "<user_focus>" not in out
