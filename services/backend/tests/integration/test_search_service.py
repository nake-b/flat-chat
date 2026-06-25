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

The transit-modes test (``test_transit_modes_filter_executes``) is the
direct regression for the bug. Everything else covers the rest of the
filter surface so the next operator-shape mistake is caught the same
way.

Run:
    docker compose exec postgres createdb -U flat_chat flat_chat_test
    export TEST_DATABASE_URL=postgresql://flat_chat:flat_chat@localhost:5432/flat_chat_test
    pytest services/backend/tests/integration/test_search_service.py
"""

from __future__ import annotations

from sqlalchemy import text

from flat_chat.search.geo_filters import (
    HospitalFilter,
    LandmarkFilter,
    NamedGeoContextFilter,
    SchoolFilter,
    TransitFilter,
)
from flat_chat.search.schemas import SearchParams
from flat_chat.search.service import SearchService

from ..conftest import DB_REQUIRED
from ..fixtures.factories import drive_search as _drive
from ..fixtures.factories import gold_row as _gold_row
from ..fixtures.factories import listing_row as _listing_row

pytestmark = DB_REQUIRED


# ---------------------------------------------------------------------------
# Sanity
# ---------------------------------------------------------------------------


def test_no_filters_returns_seeded_listing(async_db_url):
    listing = _listing_row()
    gold = _gold_row(listing["id"])

    async def body(service):
        results, total = await service.search(SearchParams())
        return [r.id for r in results], total

    ids, total = _drive(async_db_url, [(listing, gold)], body)
    assert str(listing["id"]) in ids
    assert total >= 1


def test_listing_without_gold_is_filtered_out_by_any_geo_predicate(async_db_url):
    """Listings without gold rows appear in unfiltered search (LEFT OUTER JOIN),
    but ANY geo-context predicate must exclude them because the gold side is NULL.

    Guards against an accidental switch to a predicate that's NULL-tolerant
    (which would silently surface un-enriched listings with empty chip data).
    """
    listing = _listing_row()

    async def body(service):
        results, _ = await service.search(SearchParams(near_park="near"))
        return [r.id for r in results]

    ids = _drive(async_db_url, [(listing, None)], body)
    assert str(listing["id"]) not in ids


# ---------------------------------------------------------------------------
# THE bug regression — transit.modes filter must produce valid Postgres SQL.
# Before the fix this raised: psycopg.errors.UndefinedFunction —
# operator does not exist: jsonb ?| jsonb.
# ---------------------------------------------------------------------------


def test_transit_modes_filter_executes(async_db_url):
    """Regression: transit.modes once produced invalid ``jsonb ?| jsonb`` SQL.

    With the fix this passes — the @> per-mode predicates compile to
    ``jsonb @> jsonb`` which is a defined Postgres operator.
    """
    listing = _listing_row()
    # GTFS code 400 = U-Bahn (subway).
    gold = _gold_row(
        listing["id"],
        nearest_transit_m=200,
        nearest_transit_lines=["U1"],
        nearest_transit_name="U Schlesisches Tor",
        transit_top3=[
            {
                "name": "U Schlesisches Tor",
                "distance_m": 200,
                "modes": [400],
                "lines": ["U1"],
            }
        ],
    )

    async def body(service):
        params = SearchParams(transit=TransitFilter(modes=["u_bahn"], distance="near"))
        results, _ = await service.search(params)
        return [r.id for r in results]

    ids = _drive(async_db_url, [(listing, gold)], body)
    assert str(listing["id"]) in ids


def test_transit_modes_filter_misses_when_only_other_mode(async_db_url):
    """A listing with only bus (700) in top-3 must NOT match ``modes=["u_bahn"]``."""
    listing = _listing_row()
    gold = _gold_row(
        listing["id"],
        nearest_transit_m=150,
        transit_top3=[{"name": "Bus stop", "distance_m": 150, "modes": [700]}],
    )

    async def body(service):
        params = SearchParams(transit=TransitFilter(modes=["u_bahn"], distance="near"))
        results, _ = await service.search(params)
        return [r.id for r in results]

    ids = _drive(async_db_url, [(listing, gold)], body)
    assert str(listing["id"]) not in ids


def test_transit_distance_filter(async_db_url):
    near = _listing_row()
    far = _listing_row()
    seeds = [
        (near, _gold_row(near["id"], nearest_transit_m=200)),
        (far, _gold_row(far["id"], nearest_transit_m=3000)),
    ]

    async def body(service):
        params = SearchParams(transit=TransitFilter(distance="near"))
        results, _ = await service.search(params)
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body)
    assert str(near["id"]) in ids
    assert str(far["id"]) not in ids


def test_transit_lines_filter_uses_array_overlap(async_db_url):
    """Exercises the ``&&`` (TEXT[] overlap) operator on nearest_transit_lines."""
    u1 = _listing_row()
    u8 = _listing_row()
    seeds = [
        (u1, _gold_row(u1["id"], nearest_transit_m=200, nearest_transit_lines=["U1"])),
        (u8, _gold_row(u8["id"], nearest_transit_m=200, nearest_transit_lines=["U8"])),
    ]

    async def body(service):
        params = SearchParams(transit=TransitFilter(lines=["U1"]))
        results, _ = await service.search(params)
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body)
    assert str(u1["id"]) in ids
    assert str(u8["id"]) not in ids


def test_transit_stop_name_filter_uses_ilike(async_db_url):
    listing = _listing_row()
    gold = _gold_row(
        listing["id"],
        nearest_transit_m=200,
        nearest_transit_name="U Wittenau",
    )

    async def body(service):
        params = SearchParams(transit=TransitFilter(stop_name="wittenau"))
        results, _ = await service.search(params)
        return [r.id for r in results]

    ids = _drive(async_db_url, [(listing, gold)], body)
    assert str(listing["id"]) in ids


def test_landmark_name_filter_executes(async_db_url):
    listing = _listing_row()
    gold = _gold_row(listing["id"])

    async def body(service):
        # Seed a matching ALKIS building footprint and a listing point inside it.
        await service.db.execute(
            text(
                """
                INSERT INTO buildings (name, description, geom)
                VALUES (
                    'Fernsehturm',
                    'Turm',
                    ST_GeomFromText(
                        'MULTIPOLYGON(((13.4090 52.5206, 13.4090 52.5210, 13.4098 52.5210, 13.4098 52.5206, 13.4090 52.5206)))',
                        4326
                    )
                )
                """
            )
        )
        await service.db.execute(
            text(
                """
                UPDATE listings
                SET location = ST_SetSRID(ST_MakePoint(13.4094, 52.5208), 4326)
                WHERE id = :id
                """
            ),
            {"id": listing["id"]},
        )
        params = SearchParams(
            landmark=LandmarkFilter(name="Fernsehturm", distance="near")
        )
        results, _ = await service.search(params)
        return [r.id for r in results]

    ids = _drive(async_db_url, [(listing, gold)], body)
    assert str(listing["id"]) in ids


def test_landmark_name_filter_misses_when_only_other_landmark(async_db_url):
    listing = _listing_row()
    gold = _gold_row(listing["id"])

    async def body(service):
        # Listing is near a building, but the building name doesn't match the filter.
        await service.db.execute(
            text(
                """
                INSERT INTO buildings (name, description, geom)
                VALUES (
                    'Alexanderplatz',
                    'Platz',
                    ST_GeomFromText(
                        'MULTIPOLYGON(((13.4090 52.5206, 13.4090 52.5210, 13.4098 52.5210, 13.4098 52.5206, 13.4090 52.5206)))',
                        4326
                    )
                )
                """
            )
        )
        await service.db.execute(
            text(
                """
                UPDATE listings
                SET location = ST_SetSRID(ST_MakePoint(13.4094, 52.5208), 4326)
                WHERE id = :id
                """
            ),
            {"id": listing["id"]},
        )
        params = SearchParams(
            landmark=LandmarkFilter(name="Fernsehturm", distance="near")
        )
        results, _ = await service.search(params)
        return [r.id for r in results]

    ids = _drive(async_db_url, [(listing, gold)], body)
    assert str(listing["id"]) not in ids


def test_landmark_name_filter_matches_name_variants(async_db_url):
    listing = _listing_row()
    gold = _gold_row(listing["id"])

    async def body(service):
        # ALKIS name variant with hyphen + "zu Berlin" should still match
        # user phrase "Humboldt Universität Berlin".
        await service.db.execute(
            text(
                """
                INSERT INTO buildings (name, description, geom)
                VALUES (
                    'Humboldt-Universität zu Berlin',
                    'Universität',
                    ST_GeomFromText(
                        'MULTIPOLYGON(((13.3920 52.5172, 13.3920 52.5176, 13.3926 52.5176, 13.3926 52.5172, 13.3920 52.5172)))',
                        4326
                    )
                )
                """
            )
        )
        await service.db.execute(
            text(
                """
                UPDATE listings
                SET location = ST_SetSRID(ST_MakePoint(13.3923, 52.5174), 4326)
                WHERE id = :id
                """
            ),
            {"id": listing["id"]},
        )

        params = SearchParams(
            landmark=LandmarkFilter(name="Humboldt Universität Berlin", distance=1000)
        )
        results, _ = await service.search(params)
        return [r.id for r in results]

    ids = _drive(async_db_url, [(listing, gold)], body)
    assert str(listing["id"]) in ids


def test_named_geo_school_name_filter_executes(async_db_url):
    listing = _listing_row()
    gold = _gold_row(
        listing["id"],
        schools_top3=[
            {"name": "Rosa-Parks-Grundschule", "school_type": "Grundschule", "distance_m": 600}
        ],
    )

    async def body(service):
        params = SearchParams(
            named_geo=[NamedGeoContextFilter(kind="school", name="Rosa Parks", distance="near")]
        )
        results, _ = await service.search(params)
        return [r.id for r in results]

    ids = _drive(async_db_url, [(listing, gold)], body)
    assert str(listing["id"]) in ids


def test_named_geo_park_name_filter_executes(async_db_url):
    listing = _listing_row()
    gold = _gold_row(
        listing["id"],
        parks_top2=[{"name": "Görlitzer Park", "distance_m": 500}],
    )

    async def body(service):
        params = SearchParams(
            named_geo=[NamedGeoContextFilter(kind="park", name="Görlitzer", distance="near")]
        )
        results, _ = await service.search(params)
        return [r.id for r in results]

    ids = _drive(async_db_url, [(listing, gold)], body)
    assert str(listing["id"]) in ids


def test_named_geo_water_name_filter_executes(async_db_url):
    listing = _listing_row()
    gold = _gold_row(
        listing["id"],
        water={"name": "Landwehrkanal", "water_kind": "canal", "distance_m": 1200},
    )

    async def body(service):
        params = SearchParams(
            named_geo=[NamedGeoContextFilter(kind="water", name="Landwehr", distance="walking_distance")]
        )
        results, _ = await service.search(params)
        return [r.id for r in results]

    ids = _drive(async_db_url, [(listing, gold)], body)
    assert str(listing["id"]) in ids


# ---------------------------------------------------------------------------
# Remaining geo filters — same shape, each guards its own operator class.
# ---------------------------------------------------------------------------


def test_school_catchment_filter(async_db_url):
    inside = _listing_row()
    outside = _listing_row()
    seeds = [
        (inside, _gold_row(inside["id"], school_catchment={"name": "GS Test"})),
        (outside, _gold_row(outside["id"])),
    ]

    async def body(service):
        params = SearchParams(school=SchoolFilter())
        results, _ = await service.search(params)
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body)
    assert str(inside["id"]) in ids
    assert str(outside["id"]) not in ids


def test_hospital_filter(async_db_url):
    has_hosp = _listing_row()
    no_hosp = _listing_row()
    seeds = [
        (has_hosp, _gold_row(has_hosp["id"], hospitals_top2=[{"name": "Charité"}])),
        (no_hosp, _gold_row(no_hosp["id"])),
    ]

    async def body(service):
        params = SearchParams(hospital=HospitalFilter())
        results, _ = await service.search(params)
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body)
    assert str(has_hosp["id"]) in ids
    assert str(no_hosp["id"]) not in ids


def test_near_park_filter(async_db_url):
    near = _listing_row()
    far = _listing_row()
    seeds = [
        (near, _gold_row(near["id"], nearest_park_m=200)),
        (far, _gold_row(far["id"], nearest_park_m=2000)),
    ]

    async def body(service):
        results, _ = await service.search(SearchParams(near_park="near"))
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body)
    assert str(near["id"]) in ids
    assert str(far["id"]) not in ids


def test_near_playground_jsonb_int_extraction(async_db_url):
    """Exercises ``lgc.playground["distance_m"].as_integer() <= radius``."""
    near = _listing_row()
    far = _listing_row()
    seeds = [
        (near, _gold_row(near["id"], playground={"distance_m": 200})),
        (far, _gold_row(far["id"], playground={"distance_m": 2000})),
    ]

    async def body(service):
        results, _ = await service.search(SearchParams(near_playground="near"))
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body)
    assert str(near["id"]) in ids
    assert str(far["id"]) not in ids


def test_near_water_jsonb_int_extraction(async_db_url):
    near = _listing_row()
    seeds = [(near, _gold_row(near["id"], water={"distance_m": 300}))]

    async def body(service):
        results, _ = await service.search(SearchParams(near_water="near"))
        return [r.id for r in results]

    ids = _drive(async_db_url, seeds, body)
    assert str(near["id"]) in ids


def test_max_noise_filter(async_db_url):
    quiet = _listing_row()
    loud = _listing_row()
    seeds = [
        (quiet, _gold_row(quiet["id"], noise_total_lden=45.0)),
        (loud, _gold_row(loud["id"], noise_total_lden=70.0)),
    ]

    async def body(service):
        results, _ = await service.search(SearchParams(max_noise="quiet"))
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body)
    assert str(quiet["id"]) in ids
    assert str(loud["id"]) not in ids


def test_min_greenery_jsonb_float_extraction(async_db_url):
    """Exercises ``greenery_profile['green_m2_within_300m'].as_float()``."""
    leafy = _listing_row()
    bare = _listing_row()
    seeds = [
        (
            leafy,
            _gold_row(
                leafy["id"], greenery_profile={"green_m2_within_300m": 8000.0}
            ),
        ),
        (
            bare,
            _gold_row(
                bare["id"], greenery_profile={"green_m2_within_300m": 1000.0}
            ),
        ),
    ]

    async def body(service):
        results, _ = await service.search(SearchParams(min_greenery="leafy"))
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
        results, _ = await service.search(SearchParams(density="sparse"))
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body)
    assert str(sparse["id"]) in ids
    assert str(dense["id"]) not in ids


# ---------------------------------------------------------------------------
# The exact multi-filter query that triggered the original bug report.
# Every filter from the original failing prompt is set here. If any
# operator-shape regresses, this fails first.
# ---------------------------------------------------------------------------


def test_combined_filters_kitchen_sink(async_db_url):
    """The slow-and-broken query from the bug report.

    'find 2-room flats in Kreuzberg near U-Bahn, low noise, near park,
    near school, low density, with balcony' — every
    geo-context filter set at once.
    """
    listing = _listing_row(
        rooms=2.0,
        district="Kreuzberg",
        has_balcony=True,
        warm_rent_eur=1200.0,
    )
    gold = _gold_row(
        listing["id"],
        nearest_transit_m=200,
        nearest_transit_lines=["U1"],
        transit_top3=[
            {"name": "U Schlesisches Tor", "distance_m": 200, "modes": [400]}
        ],
        nearest_park_m=180,
        school_catchment={"name": "GS Test"},
        hospitals_top2=[{"name": "Charité"}],
        noise_total_lden=48.0,
        persons_per_hectare=40.0,  # < DENSITY_SPARSE_MAX (50) so density="sparse" matches
        greenery_profile={"green_m2_within_300m": 6000.0},
        playground={"distance_m": 300},
        water={"distance_m": 800},
    )

    async def body(service):
        params = SearchParams(
            rooms_min=2.0,
            rooms_max=2.5,
            districts=["Kreuzberg"],
            has_balcony=True,
            price_warm_max=1500,
            transit=TransitFilter(modes=["u_bahn"], distance="near"),
            school=SchoolFilter(),
            near_park="near",
            near_playground="walking_distance",
            max_noise="quiet",
            min_greenery="leafy",
            density="sparse",
        )
        results, total = await service.search(params)
        return [r.id for r in results], total

    ids, total = _drive(async_db_url, [(listing, gold)], body)
    assert str(listing["id"]) in ids
    assert total == 1
