"""Unit tests for `chat/llm_context.py`.

Every byte the LLM sees about result data and per-turn state flows through
this module. Prose changes here propagate directly into the LLM's tool-call
behaviour and prompt cache, so each test pins a specific shape.

Post-tiering: the result set is `state.result_markers`; the view formats an
explicit card slice the caller hands it (`preview_cards` for the summary,
hydrated cards for deeper pages). Counts come from `total_results`.

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
    ListingCard,
    Marker,
    NearestHospital,
    NearestKita,
    NearestLandmark,
    NearestPark,
    NearestPlayground,
    NearestSchool,
    NearestTransitStop,
    NearestWater,
    NoiseProfile,
    SchoolCatchmentInfo,
)
from flat_chat.search.schemas import (
    DistrictCount,
    NumericFacet,
    ResultFacets,
    SearchParams,
)

# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------


def _card(idx: int, **overrides) -> ListingCard:
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


def _marker(idx: int) -> Marker:
    return Marker(id=f"id-{idx}", lat=52.5, lng=13.4, price_warm_eur=1000.0 + idx)


def _state(
    *, n_markers: int, n_preview: int | None = None, total: int | None = None
) -> SessionState:
    """SessionState with `n_markers` markers and `n_preview` preview cards
    (defaults to all markers, capped at 10)."""
    if n_preview is None:
        n_preview = min(n_markers, 10)
    state = SessionState()
    state.search_params = SearchParams(rooms_min=2.0)
    state.result_markers = [_marker(i + 1) for i in range(n_markers)]
    state.preview_cards = [_card(i + 1) for i in range(n_preview)]
    state.total_results = total if total is not None else n_markers
    return state


# ---------------------------------------------------------------------------
# LlmResultSetView.total / order_label
# ---------------------------------------------------------------------------


def test_total_reads_total_results():
    state = _state(n_markers=5, total=487)
    view = LlmResultSetView(state)
    assert view.total == 487


def test_order_label_no_params_is_recent():
    assert LlmResultSetView(SessionState()).order_label() == "most recent first"


def test_order_label_relevance_with_query():
    state = _state(n_markers=1)
    state.search_params = SearchParams(query="balcony", sort_by="relevance")
    assert LlmResultSetView(state).order_label() == "sorted by relevance to your query"


def test_order_label_relevance_without_query_falls_back_to_recent():
    state = _state(n_markers=1)
    state.search_params = SearchParams(sort_by="relevance")
    assert LlmResultSetView(state).order_label() == "most recent first"


def test_order_label_price_and_area():
    state = _state(n_markers=1)
    state.search_params = SearchParams(sort_by="price")
    assert LlmResultSetView(state).order_label() == "sorted by lowest warm rent"
    state.search_params = SearchParams(sort_by="area")
    assert LlmResultSetView(state).order_label() == "sorted by largest area"


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


def test_summary_zero_results_returns_no_match_prose():
    state = SessionState()
    state.search_params = SearchParams(rooms_min=2.0)
    out = LlmResultSetView(state).summary(state.preview_cards)
    assert "No apartments found" in out
    assert "Try broadening" in out


def test_summary_three_results_header_and_cards_and_footer():
    state = _state(n_markers=3)
    out = LlmResultSetView(state).summary(state.preview_cards)
    lines = out.splitlines()
    assert lines[0] == "Found 3 listings, most recent first."
    assert lines[1] == "Showing 1–3:"
    assert lines[2].startswith("  1. Apt #1 | €1100 | 2 rooms | 50m² | Kreuzberg")
    assert lines[3].startswith("  2. Apt #2")
    assert lines[4].startswith("  3. Apt #3")
    # All matches shown (total == 3 == shown).
    assert "All results shown above." in out
    assert "open_listing(indices=[N])" in out
    assert "search_apartments(...)" in out


def test_summary_caps_at_top_n_and_shows_remaining_in_footer():
    # 12 total, preview of 10; summary shows top 5 and the footer counts the
    # rest against the TOTAL (not the preview).
    state = _state(n_markers=12, n_preview=10, total=12)
    out = LlmResultSetView(state).summary(state.preview_cards, top_n=5)
    assert "Showing 1–5:" in out
    assert "7 more match" in out
    assert "get_result_page(page=N)" in out


def test_summary_first_line_is_frontend_breadcrumb_parseable():
    """Cross-language contract: the summary's first line must stay parseable by
    the frontend's `parseSearchCount` (services/frontend/src/state/
    searchBreadcrumb.ts), which renders the per-turn "Found N apartments"
    breadcrumb (issue #22). It matches `^Found (\\d+) listings?` for hits and
    `^No apartments found` for the empty case. Reword the prose → update that
    regex in the same change; this test is the tripwire."""
    import re

    found = LlmResultSetView(_state(n_markers=48, total=48)).summary(
        _state(n_markers=48).preview_cards
    )
    m = re.match(r"^Found (\d+) listings?", found.splitlines()[0])
    assert m is not None and int(m.group(1)) == 48

    empty_state = SessionState()
    empty_state.search_params = SearchParams(rooms_min=2.0)
    empty = LlmResultSetView(empty_state).summary(empty_state.preview_cards)
    assert re.match(r"^No apartments found", empty.splitlines()[0]) is not None


# ---------------------------------------------------------------------------
# page (CSV format) — now takes an explicit hydrated slice + offsets
# ---------------------------------------------------------------------------


def test_page_renders_csv_with_header_and_absolute_indices():
    state = _state(n_markers=3)
    view = LlmResultSetView(state)
    out = view.page(state.preview_cards, start=0, page=1, total_pages=1, page_size=10)
    assert "Page 1/1 — listings 1–3 of 3" in out
    assert "```csv" in out
    assert "#,title,warm €,rooms,m²,district" in out
    assert "1,Apt #1,1100,2,50,Kreuzberg" in out
    assert "3,Apt #3,1300,2,50,Kreuzberg" in out


def test_page_uses_absolute_offset_for_a_later_page():
    # Positions 11..12 of 12 — `start` drives the absolute index labels.
    state = _state(n_markers=12, n_preview=10, total=12)
    cards = [_card(11), _card(12)]
    out = LlmResultSetView(state).page(
        cards, start=10, page=3, total_pages=3, page_size=5
    )
    assert "Page 3/3 — listings 11–12 of 12" in out
    assert "11,Apt #11" in out
    assert "12,Apt #12" in out
    assert "13,Apt" not in out


def test_page_escapes_csv_commas_and_quotes_in_title():
    state = _state(n_markers=1)
    cards = [_card(1, title='2-Zi, "WBS only"')]
    out = LlmResultSetView(state).page(
        cards, start=0, page=1, total_pages=1, page_size=10
    )
    assert '"2-Zi, ""WBS only"""' in out


# ---------------------------------------------------------------------------
# detail — now takes (index, card|None) pairs
# ---------------------------------------------------------------------------


def test_detail_no_items():
    out = LlmResultSetView(SessionState()).detail([])
    assert out == "No results to show details for. Run a search first."


def test_detail_out_of_range_index_reports_inline():
    state = _state(n_markers=2)
    out = LlmResultSetView(state).detail([(99, None)])
    assert "#99: out of range (results are 1–2)." in out


def test_detail_multi_index_preserves_order():
    state = _state(n_markers=3)
    out = LlmResultSetView(state).detail([(3, _card(3)), (1, _card(1))])
    assert out.index("--- Listing #3 ---") < out.index("--- Listing #1 ---")


# ---------------------------------------------------------------------------
# format_navigation_footer
# ---------------------------------------------------------------------------


def test_navigation_footer_empty_results():
    out = format_navigation_footer(LlmResultSetView(SessionState()), shown_end=0)
    assert "search_apartments(...)" in out
    assert "get_result_page" not in out
    assert "open_listing" not in out


def test_navigation_footer_all_shown():
    state = _state(n_markers=3)
    out = format_navigation_footer(LlmResultSetView(state), shown_end=3)
    assert "All results shown above." in out
    assert "more match" not in out
    assert "open_listing(indices=[N])" in out


def test_navigation_footer_remaining_against_total():
    state = _state(n_markers=12, n_preview=10, total=12)
    out = format_navigation_footer(LlmResultSetView(state), shown_end=5)
    assert "7 more match" in out
    assert "get_result_page(page=N)" in out


# ---------------------------------------------------------------------------
# format_listing_detail_prose (unchanged — operates on ListingDetail)
# ---------------------------------------------------------------------------


def _detail_full():
    from flat_chat.listings.context import ListingDetail

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
            NearestHospital(
                name="Urban-Krankenhaus", tier="plan_hospital", distance_m=900
            )
        ],
        nearest_water=NearestWater(
            name="Landwehrkanal", water_kind="canal", distance_m=500
        ),
        nearest_kitas=[NearestKita(name="Kita Sonnenschein", distance_m=180)],
        nearest_landmarks=[
            NearestLandmark(name="Oberbaumbrücke", category="bridge", distance_m=650),
        ],
        inside_ring=True,
        listing_bezirk="Friedrichshain-Kreuzberg",
        listing_ortsteil="Kreuzberg",
        noise=NoiseProfile(label="lively", total_lden=60.0, total_lnight=52.0),
        greenery=GreeneryProfile(label="leafy", green_m2_within_300m=6000.0),
        density=DensityProfile(label="dense", persons_per_hectare=200.0),
        disabled_parking_count=3,
    )


def test_format_listing_detail_prose_full_listing_has_every_section():
    out = format_listing_detail_prose(idx=1, detail=_detail_full())
    assert "--- Listing #1 — full detail ---" in out
    assert (
        "Sunny 2-room | €1500 warm | 2 rooms | 55 m² | Kreuzberg | Manteuffelstr. 1"
        in out
    )
    assert "  - U Kottbusser Tor — U1, U8 (200m, 3min walk)" in out
    assert "Primary school catchment: GS Lenau" in out
    assert "  - GS Lenau (Grundschule) — 300m" in out
    assert "  - Görlitzer Park — 400m" in out
    assert "Nearest playground: Mariannenplatz — 250m" in out
    assert "  - Urban-Krankenhaus (plan_hospital) — 900m" in out
    assert "Nearest water: Landwehrkanal — 500m" in out
    assert "  - Kita Sonnenschein — 180m" in out
    assert "  - Oberbaumbrücke (bridge) — 650m" in out
    assert (
        "Location: inside the S-Bahn ring, Bezirk Friedrichshain-Kreuzberg, "
        "Ortsteil Kreuzberg"
    ) in out
    assert (
        "Neighbourhood character: noise: lively (52 dB at night), "
        "greenery: leafy, density: dense"
    ) in out
    assert "Disabled parking nearby: 3 spots within 300m" in out


def test_format_listing_detail_prose_minimal_listing_omits_empty_sections():
    from flat_chat.listings.context import ListingDetail

    out = format_listing_detail_prose(idx=7, detail=ListingDetail(id="x", title="Bare"))
    assert "--- Listing #7 — full detail ---" in out
    assert "Bare" in out
    assert "Nearby transit:" not in out
    assert "Neighbourhood character:" not in out
    assert "Disabled parking nearby:" not in out


# ---------------------------------------------------------------------------
# build_dynamic_state_prompt — the XML state snapshot per turn
# ---------------------------------------------------------------------------


def test_dynamic_prompt_empty_state_shows_no_search_yet():
    out = build_dynamic_state_prompt(SessionState())
    assert "<current_state>" in out
    assert "<total>0</total>" in out
    assert "No search has run yet in this conversation." in out
    assert "<user_focus>" not in out


def test_dynamic_prompt_active_search_no_focus_omits_focus_block():
    state = _state(n_markers=3, total=487)
    out = build_dynamic_state_prompt(state)
    assert "<total>487</total>" in out
    # <loaded> is the preview-card count (cards the agent can cite without
    # hydrating), NOT the marker count.
    assert "<loaded>3</loaded>" in out
    assert "<order>most recent first</order>" in out
    assert '"rooms_min": 2.0' in out
    assert "sort_by" not in out
    assert "<user_focus>" not in out


def test_dynamic_prompt_with_active_listing_emits_focus_block():
    state = _state(n_markers=3)
    state.active_id = "id-2"  # resolves against result_markers → index 2
    state.active_listing_detail = __import__(
        "flat_chat.listings.context", fromlist=["ListingDetail"]
    ).ListingDetail(id="id-2", title="Active one")
    out = build_dynamic_state_prompt(state)
    assert "<user_focus>" in out
    assert "The user is viewing listing #2 in the detail panel." in out
    assert "Do NOT call open_listing for index 2" in out
    assert "<active_listing>" in out
    assert "Active one" in out


def test_dynamic_prompt_active_id_not_in_markers_omits_focus_block():
    state = _state(n_markers=2)
    state.active_id = "id-stale"
    state.active_listing_detail = __import__(
        "flat_chat.listings.context", fromlist=["ListingDetail"]
    ).ListingDetail(id="id-stale", title="Stale")
    out = build_dynamic_state_prompt(state)
    assert "<user_focus>" not in out


# ---------------------------------------------------------------------------
# <result_facets> — whole-set aggregate stats block
# ---------------------------------------------------------------------------


def test_dynamic_prompt_renders_result_facets_block():
    state = _state(n_markers=33, n_preview=10, total=33)
    state.facets = ResultFacets(
        price_warm_eur=NumericFacet(min=620.0, median=1180.0, max=1950.0),
        area_sqm=NumericFacet(min=28.0, median=64.0, max=112.0),
        districts=[
            DistrictCount(district="Prenzlauer Berg", count=21),
            DistrictCount(district="Wedding", count=9),
            DistrictCount(district="Mitte", count=3),
        ],
    )
    out = build_dynamic_state_prompt(state)
    assert "<result_facets>" in out
    assert "price: €620–€1,950 (median €1,180)" in out
    assert "area: 28 m²–112 m² (median 64 m²)" in out
    assert "neighbourhoods (by Ortsteil): Prenzlauer Berg 21, Wedding 9, Mitte 3" in out


def test_dynamic_prompt_omits_result_facets_when_absent():
    # No facets (e.g. zero results) → no block at all, not an empty one.
    state = _state(n_markers=3)
    state.facets = None
    out = build_dynamic_state_prompt(state)
    assert "<result_facets>" not in out


def test_result_facets_caps_districts_and_counts_overflow():
    state = _state(n_markers=20, total=20)
    state.facets = ResultFacets(
        districts=[DistrictCount(district=f"O{i}", count=20 - i) for i in range(9)],
    )
    out = build_dynamic_state_prompt(state)
    # Top 6 shown, the remaining 3 summarised as "+N more".
    assert "O0 20" in out
    assert "O5 15" in out
    assert "O6 14" not in out
    assert "+3 more" in out


def test_result_facets_collapses_single_value_range():
    # When every match shares one price, render the value once, not "X–X".
    state = _state(n_markers=2, total=2)
    state.facets = ResultFacets(
        price_warm_eur=NumericFacet(min=900.0, median=900.0, max=900.0),
    )
    out = build_dynamic_state_prompt(state)
    assert "price: €900 (median €900)" in out
    assert "€900–€900" not in out
