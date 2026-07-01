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

POI filters (transit / schools / hospitals / kitas / parks / playgrounds /
water) seed ``listings_nearby_*`` junction rows. Scalar / field filters
(inside_ring / max_noise / min_greenery / density) seed
``listings_geo_context`` columns. The named-place filter (``near_place_ref``)
seeds the source table behind ``world.named_places`` and asserts a
geometry-precise ``ST_DWithin``. See
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
    ListingNearbyKita,
    ListingNearbyPark,
    ListingNearbyPlayground,
    ListingNearbySchool,
    ListingNearbyTransit,
    ListingNearbyWater,
)
from flat_chat.search.geo_filters import (
    HospitalFilter,
    KitaFilter,
    SchoolFilter,
    TransitFilter,
    WaterFilter,
)
from flat_chat.search.schemas import PREVIEW_N, SearchParams
from flat_chat.search.service import SearchService

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
    nearby_kita_row,
    nearby_park_row,
    nearby_playground_row,
    nearby_school_row,
    nearby_transit_row,
    nearby_water_row,
)
from ..fixtures.factories import (
    with_session as _with_session,
)

pytestmark = DB_REQUIRED


# ---------------------------------------------------------------------------
# Sanity
# ---------------------------------------------------------------------------


def test_no_filters_returns_seeded_listing(async_db_url):
    listing = _listing_row()
    gold = _gold_row(listing["id"])

    async def body(service):
        results, _preview, total, _ = await service.search(SearchParams())
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
        results, _preview, _, _ = await service.search(SearchParams(near_park="near"))
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
            nearby_transit_row(
                listing["id"], distance_m=200, modes=[400], lines=["U1"]
            ),
        ),
    ]

    async def body(service):
        params = SearchParams(transit=TransitFilter(modes=["u_bahn"], distance="near"))
        results, _preview, _, _ = await service.search(params)
        return [r.id for r in results]

    ids = _drive(
        async_db_url, [(listing, _gold_row(listing["id"]))], body, junctions=junctions
    )
    assert str(listing["id"]) in ids


def test_transit_modes_filter_misses_when_only_bus(async_db_url):
    """A listing with only bus (700) in its junction must NOT match
    ``modes=["u_bahn"]``."""
    listing = _listing_row()
    junctions = [
        (
            ListingNearbyTransit,
            nearby_transit_row(
                listing["id"], distance_m=150, modes=[700], lines=["100"]
            ),
        ),
    ]

    async def body(service):
        params = SearchParams(transit=TransitFilter(modes=["u_bahn"], distance="near"))
        results, _preview, _, _ = await service.search(params)
        return [r.id for r in results]

    ids = _drive(
        async_db_url, [(listing, _gold_row(listing["id"]))], body, junctions=junctions
    )
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
                listing["id"],
                stop_id="bus",
                distance_m=80,
                modes=[700],
                lines=["100"],
                rank=1,
            ),
        ),
        (
            ListingNearbyTransit,
            nearby_transit_row(
                listing["id"],
                stop_id="ubahn",
                distance_m=400,
                modes=[400],
                lines=["U8"],
                rank=2,
            ),
        ),
    ]

    async def body(service):
        params = SearchParams(transit=TransitFilter(modes=["u_bahn"], distance="near"))
        results, _preview, _, _ = await service.search(params)
        return [r.id for r in results]

    ids = _drive(
        async_db_url, [(listing, _gold_row(listing["id"]))], body, junctions=junctions
    )
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
        results, _preview, _, _ = await service.search(params)
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body, junctions=junctions)
    assert str(near_l["id"]) in ids
    assert str(far_l["id"]) not in ids


def test_transit_lines_filter_matches_specific_line(async_db_url):
    """``lines=["U8"]`` matches a listing whose junction has U8 (even if
    nearest is U1)."""
    u1_only = _listing_row()
    u8_in_radius = _listing_row()
    seeds = [
        (u1_only, _gold_row(u1_only["id"])),
        (u8_in_radius, _gold_row(u8_in_radius["id"])),
    ]
    junctions = [
        (
            ListingNearbyTransit,
            nearby_transit_row(u1_only["id"], distance_m=200, lines=["U1"]),
        ),
        # u8_in_radius has U1 nearest AND U8 within radius — old code missed this.
        (
            ListingNearbyTransit,
            nearby_transit_row(
                u8_in_radius["id"], stop_id="a", distance_m=200, lines=["U1"], rank=1
            ),
        ),
        (
            ListingNearbyTransit,
            nearby_transit_row(
                u8_in_radius["id"], stop_id="b", distance_m=550, lines=["U8"], rank=2
            ),
        ),
    ]

    async def body(service):
        params = SearchParams(transit=TransitFilter(lines=["U8"]))
        results, _preview, _, _ = await service.search(params)
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
        results, _preview, _, _ = await service.search(params)
        return [r.id for r in results]

    ids = _drive(
        async_db_url, [(listing, _gold_row(listing["id"]))], body, junctions=junctions
    )
    assert str(listing["id"]) in ids


# ---------------------------------------------------------------------------
# Schools — junction-table-backed (proximity) + catchment chip
# ---------------------------------------------------------------------------


def test_school_proximity_filter(async_db_url):
    """Default ``SchoolFilter()`` is proximity-based — needs junction rows."""
    near_school = _listing_row()
    no_school = _listing_row()
    seeds = [
        (near_school, _gold_row(near_school["id"])),
        (no_school, _gold_row(no_school["id"])),
    ]
    junctions = [
        (ListingNearbySchool, nearby_school_row(near_school["id"], distance_m=400)),
    ]

    async def body(service):
        results, _preview, _, _ = await service.search(
            SearchParams(school=SchoolFilter())
        )
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
            nearby_school_row(
                has_gymnasium["id"], distance_m=300, school_type="Gymnasium"
            ),
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
        results, _preview, _, _ = await service.search(params)
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body, junctions=junctions)
    assert str(has_gymnasium["id"]) in ids
    assert str(only_grundschule["id"]) not in ids


def test_school_requires_catchment_combines_with_proximity(async_db_url):
    """``requires_catchment=True`` AND proximity — both predicates must hold."""
    inside_with_school = _listing_row()
    outside_with_school = _listing_row()
    seeds = [
        (
            inside_with_school,
            _gold_row(inside_with_school["id"], school_catchment={"name": "GS Test"}),
        ),
        (outside_with_school, _gold_row(outside_with_school["id"])),  # no catchment
    ]
    junctions = [
        (
            ListingNearbySchool,
            nearby_school_row(inside_with_school["id"], distance_m=400),
        ),
        (
            ListingNearbySchool,
            nearby_school_row(outside_with_school["id"], distance_m=400),
        ),
    ]

    async def body(service):
        params = SearchParams(school=SchoolFilter(requires_catchment=True))
        results, _preview, _, _ = await service.search(params)
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
        results, _preview, _, _ = await service.search(
            SearchParams(hospital=HospitalFilter())
        )
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
        results, _preview, _, _ = await service.search(
            SearchParams(hospital=HospitalFilter(tier="any"))
        )
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body, junctions=junctions)
    assert str(specialty["id"]) in ids


# ---------------------------------------------------------------------------
# inside_ring — scalar bool column on listings_geo_context
# ---------------------------------------------------------------------------


def test_inside_ring_true_matches_only_inside(async_db_url):
    inside = _listing_row()
    outside = _listing_row()
    null_ring = _listing_row()
    seeds = [
        (inside, _gold_row(inside["id"], inside_ring=True)),
        (outside, _gold_row(outside["id"], inside_ring=False)),
        # gold never assigned a ring flag → must NOT match `inside_ring=True`.
        (null_ring, _gold_row(null_ring["id"], inside_ring=None)),
    ]

    async def body(service):
        results, _preview, _, _ = await service.search(SearchParams(inside_ring=True))
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body)
    assert str(inside["id"]) in ids
    assert str(outside["id"]) not in ids
    assert str(null_ring["id"]) not in ids


def test_inside_ring_false_matches_only_outside(async_db_url):
    inside = _listing_row()
    outside = _listing_row()
    seeds = [
        (inside, _gold_row(inside["id"], inside_ring=True)),
        (outside, _gold_row(outside["id"], inside_ring=False)),
    ]

    async def body(service):
        results, _preview, _, _ = await service.search(SearchParams(inside_ring=False))
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body)
    assert str(outside["id"]) in ids
    assert str(inside["id"]) not in ids


# ---------------------------------------------------------------------------
# kita — EXISTS against listings_nearby_kitas (mirrors transit/hospital)
# ---------------------------------------------------------------------------


def test_kita_filter(async_db_url):
    near = _listing_row()
    far = _listing_row()
    seeds = [(near, _gold_row(near["id"])), (far, _gold_row(far["id"]))]
    junctions = [
        (ListingNearbyKita, nearby_kita_row(near["id"], distance_m=200)),
        (ListingNearbyKita, nearby_kita_row(far["id"], distance_m=2000)),
    ]

    async def body(service):
        params = SearchParams(kita=KitaFilter(distance="near"))
        results, _preview, _, _ = await service.search(params)
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body, junctions=junctions)
    assert str(near["id"]) in ids
    assert str(far["id"]) not in ids


# ---------------------------------------------------------------------------
# near_place_ref — geometry-precise ST_DWithin against world.named_places.
# Seed an EXTENDED geometry (a LINESTRING landmark, ~the Spree) and a listing
# near the LINE but far from its centroid; assert near_place_ref matches it
# while a centroid-radius search would not.
# ---------------------------------------------------------------------------


def test_near_place_ref_uses_full_geometry_not_centroid(async_db_url):
    import sqlalchemy as sa

    # A long E-W line at lat 52.50, from lon 13.30 to 13.50. Its centroid is
    # ~lon 13.40. A listing at (52.501, 13.305) sits ~110 m from the LINE but
    # ~6.5 km from the centroid — so a 1 km radius matches via the geometry
    # and would NOT match a centroid-based query. The spatial predicate reads
    # `Listing.location` (the PostGIS Point), so we seed it explicitly — the
    # raw-insert seed path doesn't derive it from lat/lon the way silver does.
    from geoalchemy2 import WKTElement

    on_line = _listing_row(
        latitude=52.501,
        longitude=13.305,
        location=WKTElement("POINT(13.305 52.501)", srid=4326),
    )
    elsewhere = _listing_row(
        latitude=52.40,
        longitude=13.30,
        location=WKTElement("POINT(13.30 52.40)", srid=4326),
    )
    seeds = [
        (on_line, _gold_row(on_line["id"])),
        (elsewhere, _gold_row(elsewhere["id"])),
    ]

    async def body(session):
        landmark_id = await session.scalar(
            sa.text(
                """
                INSERT INTO world.landmarks (name, source, category, geom)
                VALUES (
                    'Spree', 'osm', 'river',
                    ST_SetSRID(
                        ST_GeomFromText('LINESTRING(13.30 52.50, 13.50 52.50)'),
                        4326
                    )
                )
                RETURNING id
                """
            )
        )
        service = SearchService(session)
        params = SearchParams(near_place_ref=f"landmark:{landmark_id}", radius_km=1.0)
        results, _preview, _, _ = await service.search(params)
        return {r.id for r in results}

    ids = _with_session(async_db_url, seeds, body)
    assert str(on_line["id"]) in ids
    assert str(elsewhere["id"]) not in ids


def test_near_place_ref_malformed_token_is_ignored(async_db_url):
    """A garbage / hallucinated token must not 500 — it yields no spatial
    predicate match (defensive parse), so the search still runs."""
    a = _listing_row()
    seeds = [(a, _gold_row(a["id"]))]

    async def body(service):
        # No colon → _parse_place_ref returns None → filter dropped entirely.
        results, _preview, _, _ = await service.search(
            SearchParams(near_place_ref="not-a-real-token")
        )
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body)
    # Filter ignored, so the listing still comes back (no crash).
    assert str(a["id"]) in ids


# ---------------------------------------------------------------------------
# District OR-union — Listing.district ∪ listing_bezirk ∪ listing_ortsteil
# ---------------------------------------------------------------------------


def test_districts_or_union_across_scraped_and_polygon(async_db_url):
    # Listing whose scraped `district` differs from its polygon `ortsteil`:
    # "in Tiergarten" must match it via the Ortsteil even though the source
    # labelled it "Mitte".
    by_ortsteil = _listing_row(district="Mitte")
    by_scraped = _listing_row(district="Tiergarten")
    unrelated = _listing_row(district="Spandau")
    seeds = [
        (by_ortsteil, _gold_row(by_ortsteil["id"], listing_ortsteil="Tiergarten")),
        (by_scraped, _gold_row(by_scraped["id"], listing_ortsteil="Spandau")),
        (unrelated, _gold_row(unrelated["id"], listing_ortsteil="Spandau")),
    ]

    async def body(service):
        results, _preview, _, _ = await service.search(
            SearchParams(districts=["Tiergarten"])
        )
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body)
    assert str(by_ortsteil["id"]) in ids  # matched via the polygon Ortsteil
    assert str(by_scraped["id"]) in ids  # matched via the scraped district
    assert str(unrelated["id"]) not in ids


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
        results, _preview, _, _ = await service.search(SearchParams(near_park="near"))
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
        results, _preview, _, _ = await service.search(
            SearchParams(near_playground="near")
        )
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body, junctions=junctions)
    assert str(near["id"]) in ids
    assert str(far["id"]) not in ids


def test_near_water_filter(async_db_url):
    near = _listing_row()
    seeds = [(near, _gold_row(near["id"]))]
    junctions = [(ListingNearbyWater, nearby_water_row(near["id"], distance_m=300))]

    async def body(service):
        results, _preview, _, _ = await service.search(
            SearchParams(near_water=WaterFilter(distance="near"))
        )
        return [r.id for r in results]

    ids = _drive(async_db_url, seeds, body, junctions=junctions)
    assert str(near["id"]) in ids


def test_near_water_kind_lake_matches(async_db_url):
    """`kinds=["lake"]` matches a standing-water ("Stehendes Gewässer") body."""
    near = _listing_row()
    seeds = [(near, _gold_row(near["id"]))]
    junctions = [
        (
            ListingNearbyWater,
            nearby_water_row(
                near["id"], distance_m=300, water_kind="Stehendes Gewässer"
            ),
        )
    ]

    async def body(service):
        results, _preview, _, _ = await service.search(
            SearchParams(near_water=WaterFilter(kinds=["lake"]))
        )
        return [r.id for r in results]

    ids = _drive(async_db_url, seeds, body, junctions=junctions)
    assert str(near["id"]) in ids


def test_near_water_kind_excludes_other_kind(async_db_url):
    """A flowing-water body is a river, not a lake — the mapping IN-list must
    exclude it under kinds=["lake"] and include it under kinds=["river"]."""
    river = _listing_row()
    seeds = [(river, _gold_row(river["id"]))]
    junctions = [
        (
            ListingNearbyWater,
            nearby_water_row(river["id"], distance_m=300, water_kind="Fließgewässer"),
        )
    ]

    async def as_lake(service):
        results, _preview, _, _ = await service.search(
            SearchParams(near_water=WaterFilter(kinds=["lake"]))
        )
        return [r.id for r in results]

    async def as_river(service):
        results, _preview, _, _ = await service.search(
            SearchParams(near_water=WaterFilter(kinds=["river"]))
        )
        return [r.id for r in results]

    lake_ids = _drive(async_db_url, seeds, as_lake, junctions=junctions)
    assert str(river["id"]) not in lake_ids

    river_ids = _drive(async_db_url, seeds, as_river, junctions=junctions)
    assert str(river["id"]) in river_ids


def test_near_water_no_kinds_matches_any(async_db_url):
    """No `kinds` → matches any water body (back-compat with distance-only)."""
    near = _listing_row()
    seeds = [(near, _gold_row(near["id"]))]
    junctions = [
        (
            ListingNearbyWater,
            nearby_water_row(near["id"], distance_m=300, water_kind="Hafen"),
        )
    ]

    async def body(service):
        results, _preview, _, _ = await service.search(
            SearchParams(near_water=WaterFilter(distance="near"))
        )
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
        results, _preview, _, _ = await service.search(SearchParams(max_noise="quiet"))
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
        results, _preview, _, _ = await service.search(SearchParams(max_noise="quiet"))
        return [r.id for r in results]

    ids = _drive(async_db_url, seeds, body)
    assert str(null_noise["id"]) in ids


def test_min_greenery_jsonb_float_extraction(async_db_url):
    leafy = _listing_row()
    bare = _listing_row()
    seeds = [
        (
            leafy,
            _gold_row(leafy["id"], greenery_profile={"green_m2_within_300m": 8000.0}),
        ),
        (
            bare,
            _gold_row(bare["id"], greenery_profile={"green_m2_within_300m": 1000.0}),
        ),
    ]

    async def body(service):
        results, _preview, _, _ = await service.search(
            SearchParams(min_greenery="leafy")
        )
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
        results, _preview, _, _ = await service.search(SearchParams(density="sparse"))
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
        inside_ring=True,
        listing_ortsteil="Kreuzberg",
        greenery_profile={"green_m2_within_300m": 6000.0},
        school_catchment={"name": "GS Test"},
    )
    junctions = [
        (
            ListingNearbyTransit,
            nearby_transit_row(
                listing["id"], distance_m=200, modes=[400], lines=["U1"]
            ),
        ),
        (ListingNearbySchool, nearby_school_row(listing["id"], distance_m=400)),
        (ListingNearbyHospital, nearby_hospital_row(listing["id"], distance_m=600)),
        (ListingNearbyKita, nearby_kita_row(listing["id"], distance_m=150)),
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
            kita=KitaFilter(distance="near"),
            inside_ring=True,
            near_park="near",
            near_playground="walking_distance",
            max_noise="quiet",
            min_greenery="leafy",
            density="sparse",
        )
        results, _preview, total, _ = await service.search(params)
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
        markers, preview, total, _ = await service.search(SearchParams(sort_by="price"))
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
        markers, preview, _, _ = await service.search(SearchParams(sort_by="price"))
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
        markers, preview, _, _ = await service.search(SearchParams(sort_by="recent"))
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
        markers, _preview, total, _ = await service.search(SearchParams())
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
        markers, _preview, total, _ = await service.search(SearchParams())
        return len(markers), total

    n_markers, total = _drive(async_db_url, seeds, body)
    assert n_markers == 2  # capped
    assert total == 3  # real COUNT(*) over the filtered set


# ---------------------------------------------------------------------------
# Facets — whole-set aggregate stats (price/area ranges + neighbourhood counts)
#
# These execute `percentile_cont(...).within_group(...)` and a GROUP BY against
# real Postgres — the SQL-shape bugs that compile in SQLAlchemy but Postgres
# rejects at runtime only surface here. A unique `district` tag isolates these
# seeds from any other committed rows in the test DB.
# ---------------------------------------------------------------------------

# Sentinel district used only to scope facets tests to their own seeds — no
# committed row uses it, so `districts=[_FACET_ZONE]` matches exactly the seeds.
_FACET_ZONE = "FacetTestZoneQZX"


def _facet_seed(warm: float, area: float, ortsteil: str) -> tuple[dict, dict]:
    lst = _listing_row(warm_rent_eur=warm, area_sqm=area, district=_FACET_ZONE)
    return lst, _gold_row(lst["id"], listing_ortsteil=ortsteil)


def test_facets_price_area_ranges_and_district_counts(async_db_url):
    seeds = [
        _facet_seed(600.0, 30.0, "Wedding"),
        _facet_seed(1200.0, 60.0, "Prenzlauer Berg"),
        _facet_seed(1950.0, 112.0, "Prenzlauer Berg"),
    ]

    async def body(service):
        _markers, _preview, total, facets = await service.search(
            SearchParams(districts=[_FACET_ZONE])
        )
        return total, facets

    total, facets = _drive(async_db_url, seeds, body)
    assert total == 3
    assert facets is not None

    # Numeric ranges over the FULL set (median via percentile_cont).
    assert facets.price_warm_eur.min == 600.0
    assert facets.price_warm_eur.median == 1200.0
    assert facets.price_warm_eur.max == 1950.0
    assert facets.area_sqm.min == 30.0
    assert facets.area_sqm.median == 60.0
    assert facets.area_sqm.max == 112.0

    # Neighbourhood counts grouped by Ortsteil, busiest first, summing to total.
    counts = {d.district: d.count for d in facets.districts}
    assert counts == {"Prenzlauer Berg": 2, "Wedding": 1}
    assert facets.districts[0].district == "Prenzlauer Berg"  # ordered desc
    assert sum(d.count for d in facets.districts) == total


def test_facets_honour_the_active_filters(async_db_url):
    # A price ceiling must cap the facet max — facets describe the FILTERED set,
    # not the table. With max=1300, only the 600 and 1200 rows survive.
    seeds = [
        _facet_seed(600.0, 30.0, "Wedding"),
        _facet_seed(1200.0, 60.0, "Prenzlauer Berg"),
        _facet_seed(1950.0, 112.0, "Prenzlauer Berg"),
    ]

    async def body(service):
        _markers, _preview, total, facets = await service.search(
            SearchParams(districts=[_FACET_ZONE], price_warm_max=1300.0)
        )
        return total, facets

    total, facets = _drive(async_db_url, seeds, body)
    assert total == 2
    assert facets.price_warm_eur.min == 600.0
    assert facets.price_warm_eur.max == 1200.0  # the 1950 row is filtered out
    counts = {d.district: d.count for d in facets.districts}
    assert counts == {"Prenzlauer Berg": 1, "Wedding": 1}


def test_facets_none_when_no_results(async_db_url):
    seeds = [_facet_seed(600.0, 30.0, "Wedding")]

    async def body(service):
        _markers, _preview, total, facets = await service.search(
            SearchParams(districts=["NoSuchZoneZZZ"])
        )
        return total, facets

    total, facets = _drive(async_db_url, seeds, body)
    assert total == 0
    assert facets is None


def test_facets_exclude_null_ortsteil_from_district_counts(async_db_url):
    # A listing with no Ortsteil assignment still counts toward price/area facets
    # but is excluded from the neighbourhood breakdown (counts can sum < total).
    no_ortsteil = _listing_row(
        warm_rent_eur=2000.0, area_sqm=90.0, district=_FACET_ZONE
    )
    seeds = [
        _facet_seed(800.0, 40.0, "Wedding"),
        (no_ortsteil, _gold_row(no_ortsteil["id"])),  # listing_ortsteil → NULL
    ]

    async def body(service):
        _markers, _preview, total, facets = await service.search(
            SearchParams(districts=[_FACET_ZONE])
        )
        return total, facets

    total, facets = _drive(async_db_url, seeds, body)
    assert total == 2
    # Price facet covers BOTH rows (incl. the null-ortsteil one).
    assert facets.price_warm_eur.max == 2000.0
    # District facet covers only the Ortsteil-assigned row.
    counts = {d.district: d.count for d in facets.districts}
    assert counts == {"Wedding": 1}
    assert sum(d.count for d in facets.districts) == 1  # < total (2)
