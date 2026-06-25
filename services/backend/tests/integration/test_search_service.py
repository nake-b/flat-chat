"""Integration tests for ``SearchService`` filter SQL.

Every test in this file exists to catch the same class of bug that
silently shipped during the gold-platinum refactor: an invalid SQL
operator (``jsonb ?| jsonb``) that compiled fine in SQLAlchemy but
Postgres rejected at execute time.

Pure unit tests that only call ``stmt.compile()`` cannot catch this —
SQLAlchemy doesn't type-check operator operands. Only ``EXECUTE``
against a real Postgres does. So each filter type below is exercised
end-to-end: seed a row that matches, run the search, assert the row
came back. If the SQL is malformed for any reason — operator mismatch,
JSON path syntax, array overlap — ``await self.db.execute(stmt)`` raises.

POI filters (transit / schools / hospitals / parks / playgrounds /
water) seed ``listings_nearby_*`` junction rows. Scalar / field filters
(mss / max_noise / min_greenery / density) seed ``listings_geo_context``
columns. See
``agent-compound-docs/decisions/spatial-neighbor-tables.md``.

Run:
    docker compose exec postgres createdb -U flat_chat flat_chat_test
    export TEST_DATABASE_URL=postgresql://flat_chat:flat_chat@localhost:5432/flat_chat_test
    pytest services/backend/tests/integration/test_search_service.py
"""

from __future__ import annotations

from datetime import UTC, datetime

from flat_chat.listings.models import (
    ListingNearbyHospital,
    ListingNearbyPark,
    ListingNearbyPlayground,
    ListingNearbySchool,
    ListingNearbyTransit,
    ListingNearbyWater,
)
from flat_chat.search.geo_filters import (
    HospitalFilter,
    MssFilter,
    SchoolFilter,
    TransitFilter,
)
from flat_chat.search.schemas import PREVIEW_N, SearchParams

from ..conftest import DB_REQUIRED
from ..fixtures.factories import (
    drive_search as _drive,
)
from ..fixtures.factories import (
    gold_row as _gold_row,
)
from ..fixtures.factories import (
    listing_row as _listing_row,
)
from ..fixtures.factories import (
    nearby_hospital_row,
    nearby_park_row,
    nearby_playground_row,
    nearby_school_row,
    nearby_transit_row,
    nearby_water_row,
)

pytestmark = DB_REQUIRED


# ---------------------------------------------------------------------------
# Sanity
# ---------------------------------------------------------------------------


def test_no_filters_returns_seeded_listing(async_db_url):
    listing = _listing_row()
    gold = _gold_row(listing["id"])

    async def body(service):
        results, _preview, total = await service.search(SearchParams())
        return [r.id for r in results], total

    ids, total = _drive(async_db_url, [(listing, gold)], body)
    assert str(listing["id"]) in ids
    assert total >= 1


def test_listing_without_junction_rows_is_filtered_out_by_poi_predicate(async_db_url):
    """A listing with a gold row but NO junction rows must be excluded by
    any POI filter (EXISTS against the empty junction table returns false).
    """
    listing = _listing_row()
    gold = _gold_row(listing["id"])

    async def body(service):
        results, _preview, _ = await service.search(SearchParams(near_park="near"))
        return [r.id for r in results]

    ids = _drive(async_db_url, [(listing, gold)], body)
    assert str(listing["id"]) not in ids


# ---------------------------------------------------------------------------
# Transit — junction-table-backed
# ---------------------------------------------------------------------------


def test_transit_modes_filter_matches_u_bahn(async_db_url):
    """``modes=["u_bahn"]`` matches a listing whose junction row has 400 in modes."""
    listing = _listing_row()
    junctions = [
        (
            ListingNearbyTransit,
            nearby_transit_row(listing["id"], distance_m=200, modes=[400], lines=["U1"]),
        ),
    ]

    async def body(service):
        params = SearchParams(transit=TransitFilter(modes=["u_bahn"], distance="near"))
        results, _preview, _ = await service.search(params)
        return [r.id for r in results]

    ids = _drive(async_db_url, [(listing, _gold_row(listing["id"]))], body, junctions=junctions)
    assert str(listing["id"]) in ids


def test_transit_modes_filter_misses_when_only_bus(async_db_url):
    """A listing with only bus (700) in its junction must NOT match ``modes=["u_bahn"]``."""
    listing = _listing_row()
    junctions = [
        (
            ListingNearbyTransit,
            nearby_transit_row(listing["id"], distance_m=150, modes=[700], lines=["100"]),
        ),
    ]

    async def body(service):
        params = SearchParams(transit=TransitFilter(modes=["u_bahn"], distance="near"))
        results, _preview, _ = await service.search(params)
        return [r.id for r in results]

    ids = _drive(async_db_url, [(listing, _gold_row(listing["id"]))], body, junctions=junctions)
    assert str(listing["id"]) not in ids


def test_transit_modes_matches_u_bahn_not_nearest_stop(async_db_url):
    """Regression: pre-junction code only checked the NEAREST stop.

    With junction tables, a U-Bahn stop further away still counts: the
    nearest is a bus 80 m away, but a U8 stop is 400 m away — must match
    ``modes=["u_bahn"]`` because the U-Bahn is in the within-radius set.
    """
    listing = _listing_row()
    junctions = [
        (
            ListingNearbyTransit,
            nearby_transit_row(
                listing["id"], stop_id="bus", distance_m=80, modes=[700], lines=["100"], rank=1
            ),
        ),
        (
            ListingNearbyTransit,
            nearby_transit_row(
                listing["id"], stop_id="ubahn", distance_m=400, modes=[400], lines=["U8"], rank=2
            ),
        ),
    ]

    async def body(service):
        params = SearchParams(transit=TransitFilter(modes=["u_bahn"], distance="near"))
        results, _preview, _ = await service.search(params)
        return [r.id for r in results]

    ids = _drive(async_db_url, [(listing, _gold_row(listing["id"]))], body, junctions=junctions)
    assert str(listing["id"]) in ids


def test_transit_distance_filter(async_db_url):
    near_l = _listing_row()
    far_l = _listing_row()
    seeds = [(near_l, _gold_row(near_l["id"])), (far_l, _gold_row(far_l["id"]))]
    junctions = [
        (ListingNearbyTransit, nearby_transit_row(near_l["id"], distance_m=200)),
        (ListingNearbyTransit, nearby_transit_row(far_l["id"], distance_m=3000)),
    ]

    async def body(service):
        params = SearchParams(transit=TransitFilter(distance="near"))
        results, _preview, _ = await service.search(params)
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body, junctions=junctions)
    assert str(near_l["id"]) in ids
    assert str(far_l["id"]) not in ids


def test_transit_lines_filter_matches_specific_line(async_db_url):
    """``lines=["U8"]`` matches a listing whose junction has U8 (even if nearest is U1)."""
    u1_only = _listing_row()
    u8_in_radius = _listing_row()
    seeds = [(u1_only, _gold_row(u1_only["id"])), (u8_in_radius, _gold_row(u8_in_radius["id"]))]
    junctions = [
        (ListingNearbyTransit, nearby_transit_row(u1_only["id"], distance_m=200, lines=["U1"])),
        # u8_in_radius has U1 nearest AND U8 within radius — old code missed this.
        (
            ListingNearbyTransit,
            nearby_transit_row(u8_in_radius["id"], stop_id="a", distance_m=200, lines=["U1"], rank=1),
        ),
        (
            ListingNearbyTransit,
            nearby_transit_row(u8_in_radius["id"], stop_id="b", distance_m=550, lines=["U8"], rank=2),
        ),
    ]

    async def body(service):
        params = SearchParams(transit=TransitFilter(lines=["U8"]))
        results, _preview, _ = await service.search(params)
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body, junctions=junctions)
    assert str(u1_only["id"]) not in ids
    assert str(u8_in_radius["id"]) in ids


def test_transit_stop_name_filter_uses_ilike(async_db_url):
    listing = _listing_row()
    junctions = [
        (ListingNearbyTransit, nearby_transit_row(listing["id"], name="U Wittenau")),
    ]

    async def body(service):
        params = SearchParams(transit=TransitFilter(stop_name="wittenau"))
        results, _preview, _ = await service.search(params)
        return [r.id for r in results]

    ids = _drive(async_db_url, [(listing, _gold_row(listing["id"]))], body, junctions=junctions)
    assert str(listing["id"]) in ids


# ---------------------------------------------------------------------------
# Schools — junction-table-backed (proximity) + catchment chip
# ---------------------------------------------------------------------------


def test_school_proximity_filter(async_db_url):
    """Default ``SchoolFilter()`` is proximity-based — needs junction rows."""
    near_school = _listing_row()
    no_school = _listing_row()
    seeds = [(near_school, _gold_row(near_school["id"])), (no_school, _gold_row(no_school["id"]))]
    junctions = [
        (ListingNearbySchool, nearby_school_row(near_school["id"], distance_m=400)),
    ]

    async def body(service):
        results, _preview, _ = await service.search(SearchParams(school=SchoolFilter()))
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body, junctions=junctions)
    assert str(near_school["id"]) in ids
    assert str(no_school["id"]) not in ids


def test_school_type_filter_matches_gymnasium(async_db_url):
    """``school_type='Gymnasium'`` matches only listings with a Gymnasium in radius."""
    has_gymnasium = _listing_row()
    only_grundschule = _listing_row()
    seeds = [
        (has_gymnasium, _gold_row(has_gymnasium["id"])),
        (only_grundschule, _gold_row(only_grundschule["id"])),
    ]
    junctions = [
        (
            ListingNearbySchool,
            nearby_school_row(has_gymnasium["id"], distance_m=300, school_type="Gymnasium"),
        ),
        (
            ListingNearbySchool,
            nearby_school_row(
                only_grundschule["id"], distance_m=300, school_type="Grundschule"
            ),
        ),
    ]

    async def body(service):
        params = SearchParams(school=SchoolFilter(school_type="Gymnasium"))
        results, _preview, _ = await service.search(params)
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body, junctions=junctions)
    assert str(has_gymnasium["id"]) in ids
    assert str(only_grundschule["id"]) not in ids


def test_school_requires_catchment_combines_with_proximity(async_db_url):
    """``requires_catchment=True`` AND proximity — both predicates must hold."""
    inside_with_school = _listing_row()
    outside_with_school = _listing_row()
    seeds = [
        (inside_with_school, _gold_row(inside_with_school["id"], school_catchment={"name": "GS Test"})),
        (outside_with_school, _gold_row(outside_with_school["id"])),  # no catchment
    ]
    junctions = [
        (ListingNearbySchool, nearby_school_row(inside_with_school["id"], distance_m=400)),
        (ListingNearbySchool, nearby_school_row(outside_with_school["id"], distance_m=400)),
    ]

    async def body(service):
        params = SearchParams(school=SchoolFilter(requires_catchment=True))
        results, _preview, _ = await service.search(params)
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body, junctions=junctions)
    assert str(inside_with_school["id"]) in ids
    assert str(outside_with_school["id"]) not in ids


# ---------------------------------------------------------------------------
# Hospitals — junction-table-backed with tier filter
# ---------------------------------------------------------------------------


def test_hospital_plan_filter(async_db_url):
    """Default ``HospitalFilter()`` tier=plan_hospital — matches only plan."""
    plan = _listing_row()
    specialty = _listing_row()
    seeds = [(plan, _gold_row(plan["id"])), (specialty, _gold_row(specialty["id"]))]
    junctions = [
        (
            ListingNearbyHospital,
            nearby_hospital_row(plan["id"], distance_m=600, tier="plan_hospital"),
        ),
        (
            ListingNearbyHospital,
            nearby_hospital_row(specialty["id"], distance_m=600, tier="other"),
        ),
    ]

    async def body(service):
        results, _preview, _ = await service.search(SearchParams(hospital=HospitalFilter()))
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body, junctions=junctions)
    assert str(plan["id"]) in ids
    assert str(specialty["id"]) not in ids


def test_hospital_tier_any_widens(async_db_url):
    """``tier='any'`` is a superset of ``tier='plan_hospital'``."""
    specialty = _listing_row()
    seeds = [(specialty, _gold_row(specialty["id"]))]
    junctions = [
        (
            ListingNearbyHospital,
            nearby_hospital_row(specialty["id"], distance_m=600, tier="other"),
        ),
    ]

    async def body(service):
        results, _preview, _ = await service.search(
            SearchParams(hospital=HospitalFilter(tier="any"))
        )
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body, junctions=junctions)
    assert str(specialty["id"]) in ids


# ---------------------------------------------------------------------------
# MSS — scalar columns on listings_geo_context (unchanged)
# ---------------------------------------------------------------------------


def test_mss_status_floor(async_db_url):
    aff = _listing_row()
    mix = _listing_row()
    dis = _listing_row()
    seeds = [
        (aff, _gold_row(aff["id"], mss_status="affluent")),
        (mix, _gold_row(mix["id"], mss_status="mixed")),
        (dis, _gold_row(dis["id"], mss_status="disadvantaged")),
    ]

    async def body(service):
        params = SearchParams(mss=MssFilter(status_min="mixed"))
        results, _preview, _ = await service.search(params)
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body)
    assert str(aff["id"]) in ids
    assert str(mix["id"]) in ids
    assert str(dis["id"]) not in ids


def test_mss_dynamics_exact(async_db_url):
    improving = _listing_row()
    stable = _listing_row()
    seeds = [
        (improving, _gold_row(improving["id"], mss_status="mixed", mss_dynamics="improving")),
        (stable, _gold_row(stable["id"], mss_status="mixed", mss_dynamics="stable")),
    ]

    async def body(service):
        params = SearchParams(mss=MssFilter(status_min="mixed", dynamics="improving"))
        results, _preview, _ = await service.search(params)
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body)
    assert str(improving["id"]) in ids
    assert str(stable["id"]) not in ids


# ---------------------------------------------------------------------------
# Near-* POI filters via junction tables
# ---------------------------------------------------------------------------


def test_near_park_filter(async_db_url):
    near = _listing_row()
    far = _listing_row()
    seeds = [(near, _gold_row(near["id"])), (far, _gold_row(far["id"]))]
    junctions = [
        (ListingNearbyPark, nearby_park_row(near["id"], distance_m=200)),
        (ListingNearbyPark, nearby_park_row(far["id"], distance_m=2000)),
    ]

    async def body(service):
        results, _preview, _ = await service.search(SearchParams(near_park="near"))
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body, junctions=junctions)
    assert str(near["id"]) in ids
    assert str(far["id"]) not in ids


def test_near_playground_filter(async_db_url):
    near = _listing_row()
    far = _listing_row()
    seeds = [(near, _gold_row(near["id"])), (far, _gold_row(far["id"]))]
    junctions = [
        (ListingNearbyPlayground, nearby_playground_row(near["id"], distance_m=200)),
        (ListingNearbyPlayground, nearby_playground_row(far["id"], distance_m=2000)),
    ]

    async def body(service):
        results, _preview, _ = await service.search(SearchParams(near_playground="near"))
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body, junctions=junctions)
    assert str(near["id"]) in ids
    assert str(far["id"]) not in ids


def test_near_water_filter(async_db_url):
    near = _listing_row()
    seeds = [(near, _gold_row(near["id"]))]
    junctions = [(ListingNearbyWater, nearby_water_row(near["id"], distance_m=300))]

    async def body(service):
        results, _preview, _ = await service.search(SearchParams(near_water="near"))
        return [r.id for r in results]

    ids = _drive(async_db_url, seeds, body, junctions=junctions)
    assert str(near["id"]) in ids


# ---------------------------------------------------------------------------
# Scalar / field filters — chip columns
# ---------------------------------------------------------------------------


def test_max_noise_filter_excludes_loud(async_db_url):
    quiet = _listing_row()
    loud = _listing_row()
    seeds = [
        (quiet, _gold_row(quiet["id"], noise_total_lden=45.0)),
        (loud, _gold_row(loud["id"], noise_total_lden=70.0)),
    ]

    async def body(service):
        results, _preview, _ = await service.search(SearchParams(max_noise="quiet"))
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body)
    assert str(quiet["id"]) in ids
    assert str(loud["id"]) not in ids


def test_max_noise_optimistic_includes_null(async_db_url):
    """NULL noise (post-50m-gate) is optimistically included.

    Regression direction: pre-fix the predicate was plain ``< cutoff``
    which excludes NULL rows (NULL comparisons are unknown, not true).
    The fix uses ``or_(IS NULL, < cutoff)`` so a listing without a
    trusted noise reading still passes a "quiet" filter.
    """
    null_noise = _listing_row()
    seeds = [(null_noise, _gold_row(null_noise["id"], noise_total_lden=None))]

    async def body(service):
        results, _preview, _ = await service.search(SearchParams(max_noise="quiet"))
        return [r.id for r in results]

    ids = _drive(async_db_url, seeds, body)
    assert str(null_noise["id"]) in ids


def test_min_greenery_jsonb_float_extraction(async_db_url):
    leafy = _listing_row()
    bare = _listing_row()
    seeds = [
        (leafy, _gold_row(leafy["id"], greenery_profile={"green_m2_within_300m": 8000.0})),
        (bare, _gold_row(bare["id"], greenery_profile={"green_m2_within_300m": 1000.0})),
    ]

    async def body(service):
        results, _preview, _ = await service.search(SearchParams(min_greenery="leafy"))
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body)
    assert str(leafy["id"]) in ids
    assert str(bare["id"]) not in ids


def test_density_sparse_filter(async_db_url):
    sparse = _listing_row()
    dense = _listing_row()
    seeds = [
        (sparse, _gold_row(sparse["id"], persons_per_hectare=40.0)),
        (dense, _gold_row(dense["id"], persons_per_hectare=300.0)),
    ]

    async def body(service):
        results, _preview, _ = await service.search(SearchParams(density="sparse"))
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body)
    assert str(sparse["id"]) in ids
    assert str(dense["id"]) not in ids


# ---------------------------------------------------------------------------
# Multi-filter kitchen sink — exercises every shape at once. If any
# operator / cast / junction-EXISTS regresses, this fails first.
# ---------------------------------------------------------------------------


def test_combined_filters_kitchen_sink(async_db_url):
    listing = _listing_row(
        rooms=2.0,
        district="Kreuzberg",
        has_balcony=True,
        warm_rent_eur=1200.0,
    )
    gold = _gold_row(
        listing["id"],
        noise_total_lden=48.0,
        persons_per_hectare=40.0,
        mss_status="mixed",
        mss_dynamics="improving",
        greenery_profile={"green_m2_within_300m": 6000.0},
        school_catchment={"name": "GS Test"},
    )
    junctions = [
        (
            ListingNearbyTransit,
            nearby_transit_row(listing["id"], distance_m=200, modes=[400], lines=["U1"]),
        ),
        (ListingNearbySchool, nearby_school_row(listing["id"], distance_m=400)),
        (ListingNearbyHospital, nearby_hospital_row(listing["id"], distance_m=600)),
        (ListingNearbyPark, nearby_park_row(listing["id"], distance_m=180)),
        (ListingNearbyPlayground, nearby_playground_row(listing["id"], distance_m=300)),
        (ListingNearbyWater, nearby_water_row(listing["id"], distance_m=800)),
    ]

    async def body(service):
        params = SearchParams(
            rooms_min=2.0,
            rooms_max=2.5,
            districts=["Kreuzberg"],
            has_balcony=True,
            price_warm_max=1500,
            transit=TransitFilter(modes=["u_bahn"], distance="near"),
            school=SchoolFilter(),
            hospital=HospitalFilter(),
            mss=MssFilter(status_min="mixed", dynamics="improving"),
            near_park="near",
            near_playground="walking_distance",
            max_noise="quiet",
            min_greenery="leafy",
            density="sparse",
        )
        results, _preview, total = await service.search(params)
        return [r.id for r in results], total

    ids, total = _drive(async_db_url, [(listing, gold)], body, junctions=junctions)
    assert str(listing["id"]) in ids
    assert total == 1


# ---------------------------------------------------------------------------
# Marker / preview / total shape (the tiered return)
# ---------------------------------------------------------------------------


def test_search_returns_markers_preview_and_total(async_db_url):
    listings = [_listing_row(warm_rent_eur=1000.0 + i * 100) for i in range(3)]
    seeds = [(lst, _gold_row(lst["id"])) for lst in listings]

    async def body(service):
        markers, preview, total = await service.search(SearchParams(sort_by="price"))
        return [m.id for m in markers], [c.id for c in preview], total

    marker_ids, preview_ids, total = _drive(async_db_url, seeds, body)
    assert total == 3
    assert len(marker_ids) == 3
    # Preview is a true prefix of the marker order (shared filter/sort).
    assert preview_ids == marker_ids[: len(preview_ids)]


def test_preview_is_prefix_of_markers_with_tied_price(async_db_url):
    """Regression for the missing ORDER BY tie-break.

    The marker query (LIMIT MARKER_CAP) and preview query (LIMIT PREVIEW_N)
    are separate executions. With every row sharing the same `warm_rent_eur`,
    a non-unique `ORDER BY warm_rent_eur` lets Postgres order the two queries'
    tied rows differently, so `preview[k]` would not equal `marker[k]`. The
    `Listing.id` tie-break makes both orders identical and deterministic.
    Seed well past PREVIEW_N so the preview is a strict prefix.
    """
    listings = [_listing_row(warm_rent_eur=1200.0) for _ in range(PREVIEW_N + 5)]
    seeds = [(lst, _gold_row(lst["id"])) for lst in listings]

    async def body(service):
        markers, preview, _ = await service.search(SearchParams(sort_by="price"))
        return [m.id for m in markers], [c.id for c in preview]

    marker_ids, preview_ids = _drive(async_db_url, seeds, body)
    assert len(marker_ids) == PREVIEW_N + 5
    assert len(preview_ids) == PREVIEW_N
    assert preview_ids == marker_ids[:PREVIEW_N]


def test_preview_is_prefix_of_markers_with_tied_recency(async_db_url):
    """Same prefix invariant for the recency sort with an identical
    `ingested_at` across all rows — the `Listing.id` tie-break is what makes
    `ORDER BY ingested_at DESC` deterministic between the two queries. This is
    also the order the `relevance` sort degrades to for un-embedded rows.
    """
    stamp = datetime.now(tz=UTC)
    listings = [_listing_row(ingested_at=stamp) for _ in range(PREVIEW_N + 5)]
    seeds = [(lst, _gold_row(lst["id"])) for lst in listings]

    async def body(service):
        markers, preview, _ = await service.search(SearchParams(sort_by="recent"))
        return [m.id for m in markers], [c.id for c in preview]

    marker_ids, preview_ids = _drive(async_db_url, seeds, body)
    assert len(marker_ids) == PREVIEW_N + 5
    assert preview_ids == marker_ids[:PREVIEW_N]


def test_search_drops_null_coordinate_listings_from_markers(async_db_url):
    has_coords = _listing_row()
    no_coords = _listing_row(latitude=None, longitude=None)
    seeds = [
        (has_coords, _gold_row(has_coords["id"])),
        (no_coords, _gold_row(no_coords["id"])),
    ]

    async def body(service):
        markers, _preview, total = await service.search(SearchParams())
        return {m.id for m in markers}, total

    ids, total = _drive(async_db_url, seeds, body)
    assert str(has_coords["id"]) in ids
    assert str(no_coords["id"]) not in ids
    assert total == 1


def test_search_total_uses_count_when_marker_cap_binds(async_db_url, monkeypatch):
    # Force the cap to bind so `total` comes from COUNT(*), not len(markers).
    from flat_chat.search import service as _svc

    monkeypatch.setattr(_svc, "MARKER_CAP", 2)

    listings = [_listing_row() for _ in range(3)]
    seeds = [(lst, _gold_row(lst["id"])) for lst in listings]

    async def body(service):
        markers, _preview, total = await service.search(SearchParams())
        return len(markers), total

    n_markers, total = _drive(async_db_url, seeds, body)
    assert n_markers == 2  # capped
    assert total == 3  # real COUNT(*) over the filtered set
