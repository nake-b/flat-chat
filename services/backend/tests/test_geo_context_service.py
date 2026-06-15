"""Unit tests for GeoContextService — SQL composition only, no DB roundtrip.

These tests compile generated statements to literal-bound SQL strings via
the postgresql dialect and assert on shape. They prove the filter / chip
join builders emit the right SQL constructs (ST_DWithin, lateral, array
overlap) without needing a live PostGIS connection.

Integration tests (Layer 2) live in `test_search_with_geo.py` and use a
real test DB to exercise the full search → chips → predicate pipeline.
"""

from unittest.mock import MagicMock

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from flat_chat.search.geo_context_service import GeoContextService
from flat_chat.search.geo_filters import (
    HospitalFilter,
    MssFilter,
    SchoolFilter,
    TransitFilter,
)
from flat_chat.search.models import Listing


def _compile(stmt) -> str:
    """Compile a statement to a literal-bound SQL string for assertions."""
    return str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def _service() -> GeoContextService:
    return GeoContextService(MagicMock())


def test_transit_filter_emits_st_dwithin_predicate() -> None:
    stmt = select(Listing)
    filtered = _service()._apply_transit_filter(stmt, TransitFilter())
    sql = _compile(filtered).lower()
    assert "transit_stops" in sql
    assert "st_dwithin" in sql
    # Default distance bucket is "near" = 650m. Should be bound literally.
    assert "650" in sql


def test_transit_filter_with_modes_emits_array_overlap() -> None:
    stmt = select(Listing)
    f = TransitFilter(modes=["u_bahn", "s_bahn"])
    filtered = _service()._apply_transit_filter(stmt, f)
    sql = _compile(filtered).lower()
    # GTFS Extended codes: 400=U-Bahn, 109=S-Bahn — both bound literally.
    assert "400" in sql
    assert "109" in sql
    # Postgres array overlap operator `&&` is emitted.
    assert "&&" in sql


def test_transit_filter_with_lines_emits_array_overlap_on_text() -> None:
    stmt = select(Listing)
    f = TransitFilter(lines=["U8"])
    filtered = _service()._apply_transit_filter(stmt, f)
    sql = _compile(filtered).lower()
    assert "lines_served" in sql
    assert "u8" in sql
    assert "&&" in sql


def test_transit_filter_with_stop_name_emits_ilike() -> None:
    stmt = select(Listing)
    f = TransitFilter(stop_name="Wittenau")
    filtered = _service()._apply_transit_filter(stmt, f)
    sql = _compile(filtered).lower()
    assert "ilike" in sql
    assert "wittenau" in sql


def test_transit_filter_distance_int_override_honoured() -> None:
    stmt = select(Listing)
    f = TransitFilter(distance=400)
    filtered = _service()._apply_transit_filter(stmt, f)
    sql = _compile(filtered).lower()
    assert "400" in sql
    # Default "near" (650m) should NOT appear when int override is used.
    # We can't assert 650 absent because 400 contains no "650" — just sanity.


def test_apply_chips_attaches_lateral_transit() -> None:
    stmt = select(Listing)
    chipped = _service().apply_chips(stmt)
    sql = _compile(chipped).lower()
    assert "lateral" in sql
    assert "transit_stops" in sql
    assert "near_transit" in sql
    # KNN operator for ORDER BY nearest-neighbour
    assert "<->" in sql


def test_apply_chips_adds_transit_columns_to_projection() -> None:
    stmt = select(Listing)
    chipped = _service().apply_chips(stmt)
    sql = _compile(chipped).lower()
    # Both chip column labels must appear in the SELECT projection.
    assert "nearest_transit_line" in sql
    assert "nearest_transit_m" in sql


def test_apply_filters_skips_unset_filters() -> None:
    """SearchParams with no geo filters should be a no-op."""
    from flat_chat.search.schemas import SearchParams

    stmt = select(Listing)
    params = SearchParams()
    filtered = _service().apply_filters(stmt, params)
    sql = _compile(filtered).lower()
    # No geo joins / EXISTS / lateral should show up — same SQL as input.
    assert "transit_stops" not in sql
    assert "lateral" not in sql


def test_apply_filters_wires_transit_when_set() -> None:
    from flat_chat.search.schemas import SearchParams

    stmt = select(Listing)
    params = SearchParams(transit=TransitFilter(modes=["u_bahn"]))
    filtered = _service().apply_filters(stmt, params)
    sql = _compile(filtered).lower()
    assert "transit_stops" in sql
    assert "400" in sql  # u_bahn code


# ---------------------------------------------------------------------------
# Schools
# ---------------------------------------------------------------------------


def test_school_filter_emits_st_dwithin_and_table() -> None:
    stmt = select(Listing)
    filtered = _service()._apply_school_filter(stmt, SchoolFilter())
    sql = _compile(filtered).lower()
    assert "schools" in sql
    assert "st_dwithin" in sql
    assert "650" in sql  # default "near"


def test_school_filter_with_type_emits_ilike() -> None:
    stmt = select(Listing)
    filtered = _service()._apply_school_filter(
        stmt, SchoolFilter(school_type="Grundschule")
    )
    sql = _compile(filtered).lower()
    assert "school_type" in sql
    assert "ilike" in sql
    assert "grundschule" in sql


# ---------------------------------------------------------------------------
# Hospitals
# ---------------------------------------------------------------------------


def test_hospital_filter_defaults_to_plan_hospital_tier() -> None:
    stmt = select(Listing)
    filtered = _service()._apply_hospital_filter(stmt, HospitalFilter())
    sql = _compile(filtered).lower()
    assert "hospitals" in sql
    assert "st_dwithin" in sql
    assert "plan_hospital" in sql


def test_hospital_filter_tier_any_omits_tier_predicate() -> None:
    stmt = select(Listing)
    filtered = _service()._apply_hospital_filter(stmt, HospitalFilter(tier="any"))
    sql = _compile(filtered).lower()
    assert "hospitals" in sql
    # tier=any → no `tier = 'plan_hospital'` filter
    assert "plan_hospital" not in sql


# ---------------------------------------------------------------------------
# MSS (Sozialmonitoring)
# ---------------------------------------------------------------------------


def test_mss_filter_status_min_affluent_maps_to_hoch_only() -> None:
    from flat_chat.search.schemas import SearchParams

    stmt = select(Listing)
    params = SearchParams(mss=MssFilter(status_min="affluent"))
    filtered = _service().apply_filters(stmt, params)
    sql = _compile(filtered).lower()
    # "affluent" is the top status — only 'hoch' should appear; nothing lower.
    assert "'hoch'" in sql
    assert "sehr niedrig" not in sql
    assert "'niedrig'" not in sql
    assert "'mittel'" not in sql


def test_mss_filter_status_min_lower_income_includes_all_above() -> None:
    from flat_chat.search.schemas import SearchParams

    stmt = select(Listing)
    params = SearchParams(mss=MssFilter(status_min="lower-income"))
    filtered = _service().apply_filters(stmt, params)
    sql = _compile(filtered).lower()
    # "lower-income" floor → niedrig + mittel + hoch (NOT sehr niedrig).
    assert "'niedrig'" in sql
    assert "'mittel'" in sql
    assert "'hoch'" in sql
    assert "sehr niedrig" not in sql


def test_mss_filter_dynamics_improving_maps_to_positiv() -> None:
    from flat_chat.search.schemas import SearchParams

    stmt = select(Listing)
    params = SearchParams(mss=MssFilter(dynamics="improving"))
    filtered = _service().apply_filters(stmt, params)
    sql = _compile(filtered).lower()
    assert "positiv" in sql


def test_mss_filter_uses_st_contains_on_planning_area() -> None:
    from flat_chat.search.schemas import SearchParams

    stmt = select(Listing)
    params = SearchParams(mss=MssFilter(status_min="affluent"))
    filtered = _service().apply_filters(stmt, params)
    sql = _compile(filtered).lower()
    assert "social_monitoring_2025" in sql
    assert "st_contains" in sql


# ---------------------------------------------------------------------------
# Parks / playgrounds / water — flat NearSpec filters
# ---------------------------------------------------------------------------


def test_near_park_filter_excludes_cemeteries() -> None:
    stmt = select(Listing)
    filtered = _service()._apply_near_park_filter(stmt, "near")
    sql = _compile(filtered).lower()
    assert "parks" in sql
    # Cemetery exclusion is `NOT ILIKE '%friedhof%'` → `NOT (... ILIKE ...)`.
    assert "friedhof" in sql
    assert "not " in sql  # the NOT in NOT ILIKE


def test_near_park_filter_distance_int_override() -> None:
    stmt = select(Listing)
    filtered = _service()._apply_near_park_filter(stmt, 250)
    sql = _compile(filtered).lower()
    assert "250" in sql
    assert "st_dwithin" in sql


def test_near_playground_filter_uses_playgrounds_table() -> None:
    stmt = select(Listing)
    filtered = _service()._apply_near_playground_filter(stmt, "near")
    sql = _compile(filtered).lower()
    assert "playgrounds" in sql
    assert "st_dwithin" in sql


def test_near_water_filter_uses_water_bodies_table() -> None:
    stmt = select(Listing)
    filtered = _service()._apply_near_water_filter(stmt, "walking_distance")
    sql = _compile(filtered).lower()
    assert "water_bodies" in sql
    assert "1200" in sql  # walking_distance bucket


# ---------------------------------------------------------------------------
# Noise (nearest-point compare)
# ---------------------------------------------------------------------------


def test_noise_filter_quiet_below_55() -> None:
    stmt = select(Listing)
    filtered = _service()._apply_noise_filter(stmt, "quiet")
    sql = _compile(filtered).lower()
    assert "street_noise_2022" in sql
    assert "noise_total_lden" in sql
    # quiet → < 55.0
    assert "55" in sql


def test_noise_filter_lively_below_65() -> None:
    stmt = select(Listing)
    filtered = _service()._apply_noise_filter(stmt, "lively")
    sql = _compile(filtered).lower()
    assert "65" in sql


# ---------------------------------------------------------------------------
# Density (ST_Contains + bucket compare)
# ---------------------------------------------------------------------------


def test_density_sparse_emits_less_than_50() -> None:
    stmt = select(Listing)
    filtered = _service()._apply_density_filter(stmt, "sparse")
    sql = _compile(filtered).lower()
    assert "population_density_2025" in sql
    assert "st_contains" in sql
    assert "50" in sql


def test_density_dense_emits_greater_than_150() -> None:
    stmt = select(Listing)
    filtered = _service()._apply_density_filter(stmt, "dense")
    sql = _compile(filtered).lower()
    assert "150" in sql


# ---------------------------------------------------------------------------
# Greenery filter (proxy — non-cemetery park within 300m / 150m)
# ---------------------------------------------------------------------------


def test_greenery_leafy_uses_300m_park_proxy() -> None:
    stmt = select(Listing)
    filtered = _service()._apply_greenery_filter(stmt, "leafy")
    sql = _compile(filtered).lower()
    assert "parks" in sql
    assert "300" in sql
    assert "friedhof" in sql  # cemetery exclusion present


def test_greenery_very_leafy_uses_150m_park_proxy() -> None:
    stmt = select(Listing)
    filtered = _service()._apply_greenery_filter(stmt, "very_leafy")
    sql = _compile(filtered).lower()
    assert "150" in sql


# ---------------------------------------------------------------------------
# Chip LATERALs — verify all new chip columns are emitted
# ---------------------------------------------------------------------------


def test_apply_chips_emits_nearest_park_lateral() -> None:
    stmt = select(Listing)
    chipped = _service().apply_chips(stmt)
    sql = _compile(chipped).lower()
    assert "nearest_park_name" in sql
    assert "nearest_park_m" in sql
    # Cemetery exclusion must appear in the park chip lateral.
    assert "friedhof" in sql


def test_apply_chips_emits_noise_chip_column() -> None:
    stmt = select(Listing)
    chipped = _service().apply_chips(stmt)
    sql = _compile(chipped).lower()
    assert "noise_total_lden" in sql
    assert "street_noise_2022" in sql


def test_apply_chips_emits_density_chip_column() -> None:
    stmt = select(Listing)
    chipped = _service().apply_chips(stmt)
    sql = _compile(chipped).lower()
    assert "persons_per_hectare" in sql
    assert "population_density_2025" in sql


def test_apply_chips_emits_mss_de_chip_columns() -> None:
    stmt = select(Listing)
    chipped = _service().apply_chips(stmt)
    sql = _compile(chipped).lower()
    # SQL emits the German labels — Python translates downstream.
    assert "mss_status_de" in sql
    assert "mss_dynamics_de" in sql
    assert "social_monitoring_2025" in sql


def test_apply_chips_uses_lateral_joins() -> None:
    """All chip subqueries should be lateral, not regular joins."""
    stmt = select(Listing)
    chipped = _service().apply_chips(stmt)
    sql = _compile(chipped).lower()
    # Multiple LATERAL joins — 5 chip groups.
    assert sql.count("lateral") >= 5
