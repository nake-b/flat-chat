"""Integration tests for the gold ETL (`services/ingestion/src/gold/`).

`test_search_service.py` and `test_listing_service.py` exercise gold's
*outputs*: seed junction-table rows by hand, then assert SearchService /
ListingService projects them correctly. They don't exercise gold's
*inputs* — the raw POI tables (`transit_stops`, `parks`, ...) that
`enrich_nearby_*` reads, the polygon containment in `enrich_density` /
`enrich_mss` / `enrich_school_catchment`, the 50 m coverage gate in
`enrich_noise`, the cemetery exclusion in `enrich_nearby_parks`, the
top-K ∪ within-R population rule, or the ROW_NUMBER tie-break that
keeps the chip scalars stable.

This file does. Each test seeds silver POIs + a listing, runs one
`enrich_*` function, and asserts the gold/junction output. Rolled back
per-test so the DB stays pristine.

Gold ETL is sync (psycopg2 `Connection`); the rest of the integration
suite is async (asyncpg). We open a dedicated sync engine here against
the same test DB and wrap each test in a transaction-rollback.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Connection, create_engine, text

from ..conftest import DB_REQUIRED

# Gold ETL lives in the ingestion service, which the backend does NOT
# depend on (intentional — see services/ingestion/CLAUDE.md). Add its
# src dir to sys.path so the test can import the enrich functions.
_INGESTION_SRC = Path(__file__).resolve().parents[4] / "services" / "ingestion" / "src"
if str(_INGESTION_SRC) not in sys.path:
    sys.path.insert(0, str(_INGESTION_SRC))

# ruff: noqa: E402, I001  (the import below depends on the sys.path edit above)
from gold.enrich_listings import (
    ALWAYS_INCLUDE_K,
    NOISE_COVERAGE_RADIUS_M,
    ensure_rows,
    enrich_chip_scalars,
    enrich_density,
    enrich_nearby_parks,
    enrich_nearby_transit,
    enrich_noise,
)

pytestmark = DB_REQUIRED


# ---------------------------------------------------------------------------
# Sync transactional connection fixture. Mirrors the async factory pattern
# (`drive_search` / `with_session`) but for the sync DBAPI gold uses.
# ---------------------------------------------------------------------------


def _sync_url(async_url: str) -> str:
    return async_url.replace(
        "postgresql+asyncpg://", "postgresql+psycopg2://", 1
    ).replace("postgresql://", "postgresql+psycopg2://", 1)


@pytest.fixture
def sync_conn(async_db_url: str) -> Iterator[Connection]:
    """Open a sync connection wrapped in a single transaction; ROLLBACK on exit.

    Pin search_path to ``world, public`` via connect_args — exactly what the
    ingestion service's engine (services/ingestion/src/db.py) does at runtime.
    The gold enrich functions and this test's seed SQL use unqualified table
    names; this resolves them to ``world`` just like production.
    """
    engine = create_engine(
        _sync_url(async_db_url),
        connect_args={"options": "-csearch_path=world,public"},
    )
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                yield conn
            finally:
                trans.rollback()
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Seed helpers — minimal silver shapes the gold ETL reads from. PostGIS
# geometries are built with ST_SetSRID(ST_MakePoint(lon, lat), 4326)
# rather than EWKT strings so the test owns the spelling.
# ---------------------------------------------------------------------------


# Roughly central Berlin — convenient lat/lon to anchor seeded geometries.
_BERLIN_LAT = 52.5200
_BERLIN_LON = 13.4050


def _point(conn: Connection, lat: float, lon: float) -> str:
    """Render an SRID=4326 point literal usable in a raw VALUES clause."""
    return f"ST_SetSRID(ST_MakePoint({lon}, {lat}), 4326)"


def _seed_listing(
    conn: Connection,
    *,
    lat: float = _BERLIN_LAT,
    lon: float = _BERLIN_LON,
) -> uuid.UUID:
    listing_id = uuid.uuid4()
    conn.execute(
        text(
            f"""
            INSERT INTO listings (id, source_name, external_id, scraped_at, location)
            VALUES (:id, 'test', :ext, now(), {_point(conn, lat, lon)})
            """
        ),
        {"id": listing_id, "ext": str(listing_id)},
    )
    return listing_id


def _offset_lat_for_meters(meters: float) -> float:
    """North-south offset in degrees of latitude for ~`meters` metres."""
    # 1° latitude ≈ 111_320 m everywhere. East-west would need cos(lat); we
    # only need the magnitude here, so a pure-north offset keeps the math
    # trivial and the resulting geography distance comes out within ~1 m of
    # `meters` at Berlin's latitude.
    return meters / 111_320.0


# ---------------------------------------------------------------------------
# ensure_rows — every listing with a non-NULL location seeds a gold row.
# ---------------------------------------------------------------------------


def test_ensure_rows_seeds_missing_gold_row(sync_conn: Connection):
    listing_id = _seed_listing(sync_conn)
    inserted = ensure_rows(sync_conn)
    assert inserted >= 1

    row = sync_conn.execute(
        text("SELECT listing_id FROM listings_geo_context WHERE listing_id = :id"),
        {"id": listing_id},
    ).first()
    assert row is not None


def test_ensure_rows_skips_listings_without_location(sync_conn: Connection):
    no_loc_id = uuid.uuid4()
    sync_conn.execute(
        text(
            "INSERT INTO listings (id, source_name, external_id, scraped_at, location) "
            "VALUES (:id, 'test', :ext, now(), NULL)"
        ),
        {"id": no_loc_id, "ext": str(no_loc_id)},
    )
    ensure_rows(sync_conn)

    row = sync_conn.execute(
        text("SELECT listing_id FROM listings_geo_context WHERE listing_id = :id"),
        {"id": no_loc_id},
    ).first()
    assert row is None


# ---------------------------------------------------------------------------
# enrich_nearby_transit — top-K=5 ∪ all-within-R per listing
# ---------------------------------------------------------------------------


def _seed_transit_stop(
    conn: Connection,
    stop_id: str,
    *,
    distance_m: float,
    modes: list[int] | None = None,
    lines: list[str] | None = None,
    name: str = "Test Stop",
) -> None:
    lat = _BERLIN_LAT + _offset_lat_for_meters(distance_m)
    conn.execute(
        text(
            f"""
            INSERT INTO transit_stops
                (stop_id, name, geom, modes_served, lines_served)
            VALUES
                (:sid, :name, {_point(conn, lat, _BERLIN_LON)},
                 CAST(:modes AS SMALLINT[]), CAST(:lines AS TEXT[]))
            """
        ),
        {
            "sid": stop_id,
            "name": name,
            "modes": modes if modes is not None else [400],
            "lines": lines if lines is not None else ["U1"],
        },
    )


def test_enrich_nearby_transit_top_k_when_sparse(sync_conn: Connection):
    """Sparse periphery: only 3 stops in the world → all 3 returned, rank 1..3.

    Top-K=5 means "at least 5 if available"; with fewer stops we return
    however many exist. Locks the "GREATEST(K, count_within_R)" floor.
    """
    listing_id = _seed_listing(sync_conn)
    for i, distance in enumerate([100, 500, 2000], start=1):
        _seed_transit_stop(sync_conn, f"s{i}", distance_m=distance)
    ensure_rows(sync_conn)

    rowcount = enrich_nearby_transit(sync_conn)
    assert rowcount == 3

    rows = sync_conn.execute(
        text(
            "SELECT stop_id, distance_m, rank FROM listings_nearby_transit "
            "WHERE listing_id = :id ORDER BY rank"
        ),
        {"id": listing_id},
    ).all()
    assert [r.stop_id for r in rows] == ["s1", "s2", "s3"]
    assert [r.rank for r in rows] == [1, 2, 3]


def test_enrich_nearby_transit_top_k_floor_when_periphery(sync_conn: Connection):
    """7 stops in the world, all *outside* R → K=5 floor still carries 5.

    Exercises the `GREATEST(K, count_within_R)` clause when
    count_within_R is zero (periphery listing, transit-desert). The K
    floor exists so the detail panel always renders a few "nearest"
    stops even when none are usefully close.
    """
    listing_id = _seed_listing(sync_conn)
    # R = 5 km for transit. Seed 7 stops all well beyond that.
    for i in range(7):
        _seed_transit_stop(sync_conn, f"s{i}", distance_m=6000 + i * 200)
    ensure_rows(sync_conn)

    enrich_nearby_transit(sync_conn)

    count = sync_conn.execute(
        text("SELECT COUNT(*) FROM listings_nearby_transit WHERE listing_id = :id"),
        {"id": listing_id},
    ).scalar()
    assert count == ALWAYS_INCLUDE_K  # 5

    # The 5 included are the nearest ones (s0..s4); the 2 farthest are
    # dropped because they exceed both R and the K floor.
    stop_ids = (
        sync_conn.execute(
            text(
                "SELECT stop_id FROM listings_nearby_transit "
                "WHERE listing_id = :id ORDER BY rank"
            ),
            {"id": listing_id},
        )
        .scalars()
        .all()
    )
    assert stop_ids == ["s0", "s1", "s2", "s3", "s4"]


def test_enrich_nearby_transit_includes_all_within_r(sync_conn: Connection):
    """7 stops all within R → all 7 included (within-R branch wins over K).

    The `∪` half of "top-K ∪ all-within-R". A dense neighbourhood
    carries every stop within the storage radius so filter EXISTS
    queries against modes/lines/distance see the full set.
    """
    listing_id = _seed_listing(sync_conn)
    for i in range(7):
        # All well inside R = 5 km for transit.
        _seed_transit_stop(sync_conn, f"s{i}", distance_m=500 + i * 300)
    ensure_rows(sync_conn)

    enrich_nearby_transit(sync_conn)

    count = sync_conn.execute(
        text("SELECT COUNT(*) FROM listings_nearby_transit WHERE listing_id = :id"),
        {"id": listing_id},
    ).scalar()
    assert count == 7


def test_enrich_nearby_transit_preserves_modes_and_lines(sync_conn: Connection):
    """Junction row carries the source `modes_served` / `lines_served` arrays.

    The search predicate uses these for `modes && [...]` / `lines && [...]`
    overlap filters; if the column copy drops one, "near U7" silently
    stops matching listings near U7 stops.
    """
    listing_id = _seed_listing(sync_conn)
    _seed_transit_stop(
        sync_conn,
        "s_u7",
        distance_m=200,
        modes=[400, 700],
        lines=["U7", "100"],
        name="U Mehringdamm",
    )
    ensure_rows(sync_conn)
    enrich_nearby_transit(sync_conn)

    row = sync_conn.execute(
        text(
            "SELECT modes, lines, name FROM listings_nearby_transit "
            "WHERE listing_id = :id AND stop_id = 's_u7'"
        ),
        {"id": listing_id},
    ).first()
    assert sorted(row.modes) == [400, 700]
    assert sorted(row.lines) == ["100", "U7"]
    assert row.name == "U Mehringdamm"


def test_enrich_nearby_transit_rank_is_deterministic_under_tie(sync_conn: Connection):
    """Two stops at the same integer distance must rank stably across runs.

    Without the (distance_m, stop_id) secondary sort on the outer
    ROW_NUMBER, the chip-scalars step (rank=1 wins) would pick a
    different stop on each `gold.run` for ties → shadow-diff churn,
    nearest_transit_lines flapping in the UI. Run the enrichment twice,
    assert the rank=1 stop is identical both times.
    """
    listing_id = _seed_listing(sync_conn)
    # Both stops at exactly the same coordinate — guarantees an integer
    # distance tie and a `geom <-> location` KNN tie.
    same_lat = _BERLIN_LAT + _offset_lat_for_meters(300)
    for sid in ["s_aaa", "s_zzz"]:
        sync_conn.execute(
            text(
                f"""
                INSERT INTO transit_stops
                    (stop_id, name, geom, modes_served, lines_served)
                VALUES
                    (:sid, :sid, {_point(sync_conn, same_lat, _BERLIN_LON)},
                     CAST(ARRAY[400] AS SMALLINT[]),
                     CAST(ARRAY['U1'] AS TEXT[]))
                """
            ),
            {"sid": sid},
        )
    ensure_rows(sync_conn)

    enrich_nearby_transit(sync_conn)
    first = sync_conn.execute(
        text(
            "SELECT stop_id FROM listings_nearby_transit "
            "WHERE listing_id = :id AND rank = 1"
        ),
        {"id": listing_id},
    ).scalar()

    enrich_nearby_transit(sync_conn)
    second = sync_conn.execute(
        text(
            "SELECT stop_id FROM listings_nearby_transit "
            "WHERE listing_id = :id AND rank = 1"
        ),
        {"id": listing_id},
    ).scalar()

    assert first == second
    # Tie-break is `, stop_id` ASC → lexicographically smaller wins.
    assert first == "s_aaa"


# ---------------------------------------------------------------------------
# enrich_nearby_parks — cemetery exclusion (object_type ILIKE '%friedhof%')
# ---------------------------------------------------------------------------


def _seed_park(
    conn: Connection,
    name: str,
    *,
    distance_m: float,
    object_type: str = "Volkspark",
) -> None:
    """Tiny 5×5 m polygon, offset N from Berlin centre by `distance_m` metres."""
    lat = _BERLIN_LAT + _offset_lat_for_meters(distance_m)
    d = 0.00002  # ~2 m — small bounding box, irrelevant to the test
    poly = (
        f"ST_SetSRID(ST_GeomFromText("
        f"'MULTIPOLYGON((("
        f"{_BERLIN_LON - d} {lat - d}, {_BERLIN_LON + d} {lat - d}, "
        f"{_BERLIN_LON + d} {lat + d}, {_BERLIN_LON - d} {lat + d}, "
        f"{_BERLIN_LON - d} {lat - d}"
        f")))'), 4326)"
    )
    conn.execute(
        text(
            f"""
            INSERT INTO parks (pit_id, name, object_type, geom)
            VALUES (:pit, :name, :otype, {poly})
            """
        ),
        {"pit": name.lower(), "name": name, "otype": object_type},
    )


def test_enrich_nearby_parks_excludes_cemeteries(sync_conn: Connection):
    """Cemeteries (object_type ILIKE '%friedhof%') don't enter the parks junction.

    Threshold doc §5: cemeteries are quiet greenspace but a poor proxy
    for "park". They're excluded here AND given half weight in
    greenery composite — surface that contract.
    """
    listing_id = _seed_listing(sync_conn)
    _seed_park(sync_conn, "Görlitzer Park", distance_m=200)
    _seed_park(
        sync_conn,
        "St-Marien-Friedhof",
        distance_m=150,
        object_type="Friedhof",
    )
    _seed_park(
        sync_conn,
        "Mixed Case Friedhof",
        distance_m=400,
        object_type="Anderer FRIEDHOF Eintrag",  # case-insensitive ILIKE
    )
    ensure_rows(sync_conn)
    enrich_nearby_parks(sync_conn)

    rows = sync_conn.execute(
        text("SELECT object_type FROM listings_nearby_parks WHERE listing_id = :id"),
        {"id": listing_id},
    ).all()
    types = [r.object_type for r in rows]
    assert "Volkspark" in types
    for t in types:
        assert "friedhof" not in t.lower()


# ---------------------------------------------------------------------------
# enrich_noise — the 50 m coverage gate (the bug that already happened once)
# ---------------------------------------------------------------------------


def _seed_noise_sample(
    conn: Connection,
    *,
    distance_m: float,
    total_lden: float,
) -> None:
    lat = _BERLIN_LAT + _offset_lat_for_meters(distance_m)
    conn.execute(
        text(
            f"""
            INSERT INTO strategic_noise_2022
                (noise_total_lden, noise_street_lden, noise_rail_lden, geom)
            VALUES (:t, :s, :r, {_point(conn, lat, _BERLIN_LON)})
            """
        ),
        {"t": total_lden, "s": total_lden, "r": None},
    )


def test_enrich_noise_within_gate_writes_value(sync_conn: Connection):
    listing_id = _seed_listing(sync_conn)
    _seed_noise_sample(sync_conn, distance_m=20, total_lden=58.0)
    ensure_rows(sync_conn)
    enrich_noise(sync_conn)

    row = sync_conn.execute(
        text(
            "SELECT noise_total_lden, noise_profile FROM listings_geo_context "
            "WHERE listing_id = :id"
        ),
        {"id": listing_id},
    ).first()
    assert row.noise_total_lden == pytest.approx(58.0)
    assert row.noise_profile is not None
    assert row.noise_profile["total_lden"] == 58.0


def test_enrich_noise_outside_gate_leaves_null(sync_conn: Connection):
    """A sample > 50 m away must NOT populate the column.

    The search filter is optimistic-include on NULL; if the gate
    silently disappears, a noisy sample 200 m away (different acoustic
    block) starts excluding listings from the "quiet" filter.
    """
    listing_id = _seed_listing(sync_conn)
    # Comfortably outside the 50 m gate — pick something the bbox
    # pre-filter (0.001 deg ≈ 110 m) still includes so we know the
    # *exact* distance check is what drops it.
    _seed_noise_sample(sync_conn, distance_m=80, total_lden=75.0)
    ensure_rows(sync_conn)
    enrich_noise(sync_conn)

    row = sync_conn.execute(
        text(
            "SELECT noise_total_lden FROM listings_geo_context WHERE listing_id = :id"
        ),
        {"id": listing_id},
    ).first()
    assert row.noise_total_lden is None
    # Sanity: the gate constant is what the production code uses.
    assert NOISE_COVERAGE_RADIUS_M == 50


# ---------------------------------------------------------------------------
# enrich_chip_scalars — reads from junction tables, writes to gold scalars
# ---------------------------------------------------------------------------


def test_chip_scalars_picks_rank_one_transit_row(sync_conn: Connection):
    """`nearest_transit_*` chips come from the rank=1 junction row, not min(distance_m).

    Today rank=1 ⇔ min distance, but the chip update joins on rank=1
    rather than re-sorting. If a future refactor changes ranking
    (e.g. weighted-by-mode), the chips should follow. Lock the join.
    """
    listing_id = _seed_listing(sync_conn)
    _seed_transit_stop(
        sync_conn,
        "s_close",
        distance_m=150,
        modes=[400],
        lines=["U7"],
        name="U Closest",
    )
    _seed_transit_stop(
        sync_conn,
        "s_far",
        distance_m=600,
        modes=[700],
        lines=["100"],
        name="Bus Farther",
    )
    ensure_rows(sync_conn)
    enrich_nearby_transit(sync_conn)
    enrich_chip_scalars(sync_conn)

    row = sync_conn.execute(
        text(
            "SELECT nearest_transit_m, nearest_transit_lines, nearest_transit_name "
            "FROM listings_geo_context WHERE listing_id = :id"
        ),
        {"id": listing_id},
    ).first()
    assert row.nearest_transit_name == "U Closest"
    assert row.nearest_transit_lines == ["U7"]
    assert row.nearest_transit_m is not None
    assert row.nearest_transit_m < 200


def test_chip_scalars_no_junction_rows_leaves_chips_null(sync_conn: Connection):
    """Listing with no nearby transit → nearest_transit_* stays NULL.

    Search filter `transit` is EXISTS-against-junction (drops the
    listing); chips here are purely card-row label rendering. The
    UPDATE FROM junction only fires when a matching row exists.
    """
    listing_id = _seed_listing(sync_conn)
    ensure_rows(sync_conn)
    enrich_nearby_transit(sync_conn)  # no source rows seeded
    enrich_chip_scalars(sync_conn)

    row = sync_conn.execute(
        text(
            "SELECT nearest_transit_m FROM listings_geo_context WHERE listing_id = :id"
        ),
        {"id": listing_id},
    ).first()
    assert row.nearest_transit_m is None


# ---------------------------------------------------------------------------
# enrich_density — polygon containment
# ---------------------------------------------------------------------------


def _seed_density_polygon(
    conn: Connection,
    *,
    population_per_hectare: float,
    population: int = 1000,
    center_lat: float = _BERLIN_LAT,
    center_lon: float = _BERLIN_LON,
) -> None:
    # 0.005 degrees ≈ 500 m square — comfortably containing the seeded listing.
    d = 0.005
    poly = (
        f"ST_SetSRID(ST_GeomFromText("
        f"'MULTIPOLYGON((("
        f"{center_lon - d} {center_lat - d}, {center_lon + d} {center_lat - d}, "
        f"{center_lon + d} {center_lat + d}, {center_lon - d} {center_lat + d}, "
        f"{center_lon - d} {center_lat - d}"
        f")))'), 4326)"
    )
    conn.execute(
        text(
            f"""
            INSERT INTO population_density_2025
                (lor_key, population, area_hectares, population_per_hectare,
                 age_under_6, age_6_to_10, age_10_to_18, age_18_to_65,
                 age_65_to_70, age_70_to_75, age_75_to_80, age_80_plus, geom)
            VALUES ('test_lor', :pop, 10.0, :pph,
                    10, 10, 10, 100, 10, 10, 10, 10, {poly})
            """
        ),
        {"pop": population, "pph": population_per_hectare},
    )


def test_enrich_density_populates_chip_and_profile(sync_conn: Connection):
    listing_id = _seed_listing(sync_conn)
    _seed_density_polygon(sync_conn, population_per_hectare=185.5)
    ensure_rows(sync_conn)
    enrich_density(sync_conn)

    row = sync_conn.execute(
        text(
            "SELECT persons_per_hectare, density_profile "
            "FROM listings_geo_context WHERE listing_id = :id"
        ),
        {"id": listing_id},
    ).first()
    assert row.persons_per_hectare == pytest.approx(185.5)
    assert row.density_profile["persons_per_hectare"] == 185.5
    assert row.density_profile["age_18_to_65"] == 100


def test_enrich_density_no_containing_polygon_leaves_null(sync_conn: Connection):
    """Listing outside every density polygon → persons_per_hectare stays NULL."""
    listing_id = _seed_listing(sync_conn)
    # Polygon far north of the listing.
    _seed_density_polygon(
        sync_conn,
        population_per_hectare=200.0,
        center_lat=_BERLIN_LAT + 0.5,  # ~55 km north — way outside
    )
    ensure_rows(sync_conn)
    enrich_density(sync_conn)

    row = sync_conn.execute(
        text(
            "SELECT persons_per_hectare FROM listings_geo_context "
            "WHERE listing_id = :id"
        ),
        {"id": listing_id},
    ).first()
    assert row.persons_per_hectare is None


# NOTE: MSS removed entirely in geo-context v2 (the `enrich_mss` enricher and
# the `social_monitoring_2025` source table are gone in the 0007 migration).
# The new admin-area / ring / kita / landmark enrichers live in the ingestion
# service and are exercised by ingestion's own integration suite (Phase 1).
