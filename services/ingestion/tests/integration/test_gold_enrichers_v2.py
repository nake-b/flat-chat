"""Integration tests for the geo-context v2 gold enrichers.

Each test executes the real enricher SQL against a Postgres fixture (gated on
``TEST_DATABASE_URL``), seeding only the silver rows the enricher reads. This
catches what a stmt-compile assertion can't — e.g. a column that exists in the
ORM but not in Postgres, or a PostGIS operator the planner rejects at runtime.

The fixtures live in the ``world`` schema; ``db_conn`` pins
``search_path = world, public`` so the unqualified table names in the
enrichers resolve there.
"""

from __future__ import annotations

import sqlalchemy as sa
from tests.conftest import DB_REQUIRED

import gold.enrich_listings as gold

pytestmark = DB_REQUIRED


# Berlin centre-ish anchor for the test listing. Silver features are placed a
# few hundred metres away so distances are small + deterministic.
_LISTING_LON = 13.4050
_LISTING_LAT = 52.5200


def _seed_listing(
    conn: sa.Connection,
    lon: float = _LISTING_LON,
    lat: float = _LISTING_LAT,
) -> str:
    """Insert one listing with a location and seed its lgc row. Returns its id."""
    listing_id = conn.execute(
        sa.text(
            """
            INSERT INTO listings (source_name, external_id, scraped_at, location)
            VALUES ('test', :ext, now(),
                    ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))
            RETURNING id
            """
        ),
        {"ext": f"t-{lon}-{lat}", "lon": lon, "lat": lat},
    ).scalar_one()
    conn.execute(
        sa.text("INSERT INTO listings_geo_context (listing_id) VALUES (:id)"),
        {"id": listing_id},
    )
    return str(listing_id)


def _point_near(lon_off: float = 0.001, lat_off: float = 0.0) -> str:
    """SQL fragment for a point offset from the test anchor."""
    return (
        f"ST_SetSRID(ST_MakePoint({_LISTING_LON + lon_off}, "
        f"{_LISTING_LAT + lat_off}), 4326)"
    )


def _polygon_covering_anchor() -> str:
    """A square polygon comfortably covering the test anchor point."""
    return (
        "ST_SetSRID(ST_GeomFromText('POLYGON(("
        f"{_LISTING_LON - 0.01} {_LISTING_LAT - 0.01},"
        f"{_LISTING_LON + 0.01} {_LISTING_LAT - 0.01},"
        f"{_LISTING_LON + 0.01} {_LISTING_LAT + 0.01},"
        f"{_LISTING_LON - 0.01} {_LISTING_LAT + 0.01},"
        f"{_LISTING_LON - 0.01} {_LISTING_LAT - 0.01}))'), 4326)"
    )


def test_enrich_nearby_kitas_populates_junction(db_conn: sa.Connection) -> None:
    listing_id = _seed_listing(db_conn)
    db_conn.execute(
        sa.text(
            f"INSERT INTO kitas (name, geom) VALUES "
            f"('Kita Sonnenschein', {_point_near(0.001)}),"
            f"('Kita Regenbogen', {_point_near(0.002)})"
        )
    )

    rows = gold.enrich_nearby_kitas(db_conn)
    assert rows == 2

    result = db_conn.execute(
        sa.text(
            """
            SELECT name, rank, distance_m
            FROM listings_nearby_kitas
            WHERE listing_id = :id
            ORDER BY rank
            """
        ),
        {"id": listing_id},
    ).all()
    assert [r.name for r in result] == ["Kita Sonnenschein", "Kita Regenbogen"]
    assert result[0].rank == 1
    assert result[0].distance_m < result[1].distance_m


def test_enrich_nearby_landmarks_filters_to_notable_categories(
    db_conn: sa.Connection,
) -> None:
    listing_id = _seed_listing(db_conn)
    db_conn.execute(
        sa.text(
            f"INSERT INTO landmarks (name, source, category, geom) VALUES "
            f"('Siegessäule', 'alkis', 'monument', {_point_near(0.001)}),"
            f"('Some Building', 'alkis', 'building', {_point_near(0.0005)}),"
            f"('Olympiastadion', 'osm', 'stadium', {_point_near(0.002)})"
        )
    )

    rows = gold.enrich_nearby_landmarks(db_conn)
    # Only the monument + stadium qualify; the generic 'building' is excluded.
    assert rows == 2

    names = db_conn.execute(
        sa.text(
            """
            SELECT name, category FROM listings_nearby_landmarks
            WHERE listing_id = :id ORDER BY rank
            """
        ),
        {"id": listing_id},
    ).all()
    assert {r.name for r in names} == {"Siegessäule", "Olympiastadion"}
    assert all(r.category in gold.NOTABLE_LANDMARK_CATEGORIES for r in names)


def test_enrich_admin_areas_sets_bezirk_and_ortsteil(db_conn: sa.Connection) -> None:
    listing_id = _seed_listing(db_conn)
    db_conn.execute(
        sa.text(
            f"INSERT INTO bezirke (name, bezirk_id, geom) "
            f"VALUES ('Mitte', '01', {_polygon_covering_anchor()})"
        )
    )
    db_conn.execute(
        sa.text(
            f"INSERT INTO ortsteile (name, geom) "
            f"VALUES ('Tiergarten', {_polygon_covering_anchor()})"
        )
    )

    gold.enrich_admin_areas(db_conn)

    row = db_conn.execute(
        sa.text(
            "SELECT listing_bezirk, listing_ortsteil "
            "FROM listings_geo_context WHERE listing_id = :id"
        ),
        {"id": listing_id},
    ).one()
    assert row.listing_bezirk == "Mitte"
    assert row.listing_ortsteil == "Tiergarten"


def test_enrich_admin_areas_picks_smallest_containing_bezirk(
    db_conn: sa.Connection,
) -> None:
    """Overlapping polygons resolve to the smallest (most specific) area."""
    listing_id = _seed_listing(db_conn)
    big = (
        "ST_SetSRID(ST_GeomFromText('POLYGON(("
        f"{_LISTING_LON - 0.1} {_LISTING_LAT - 0.1},"
        f"{_LISTING_LON + 0.1} {_LISTING_LAT - 0.1},"
        f"{_LISTING_LON + 0.1} {_LISTING_LAT + 0.1},"
        f"{_LISTING_LON - 0.1} {_LISTING_LAT + 0.1},"
        f"{_LISTING_LON - 0.1} {_LISTING_LAT - 0.1}))'), 4326)"
    )
    db_conn.execute(
        sa.text(
            f"INSERT INTO bezirke (name, geom) VALUES "
            f"('BigBezirk', {big}), ('SmallBezirk', {_polygon_covering_anchor()})"
        )
    )

    gold.enrich_admin_areas(db_conn)

    bezirk = db_conn.execute(
        sa.text(
            "SELECT listing_bezirk FROM listings_geo_context WHERE listing_id = :id"
        ),
        {"id": listing_id},
    ).scalar_one()
    assert bezirk == "SmallBezirk"


def test_bezirke_name_holds_human_label_not_numeric_id(
    db_conn: sa.Connection,
) -> None:
    """Regression guard for the `{"namgem": "name", "name": "bezirk_id"}` fix.

    The ALKIS bezirke layer publishes a numeric `name` and the human label in
    `namgem`; the alias swap lands the human label in `bezirke.name`. This
    asserts the enricher surfaces a human label ("Mitte"), never a numeric id.
    """
    listing_id = _seed_listing(db_conn)
    db_conn.execute(
        sa.text(
            f"INSERT INTO bezirke (name, bezirk_id, geom) "
            f"VALUES ('Mitte', '01', {_polygon_covering_anchor()})"
        )
    )

    gold.enrich_admin_areas(db_conn)

    bezirk = db_conn.execute(
        sa.text(
            "SELECT listing_bezirk FROM listings_geo_context WHERE listing_id = :id"
        ),
        {"id": listing_id},
    ).scalar_one()
    assert bezirk == "Mitte"
    assert not bezirk.isdigit(), "bezirk name must be the human label, not a numeric id"


def test_enrich_inside_ring_true_when_covered(db_conn: sa.Connection) -> None:
    listing_id = _seed_listing(db_conn)
    db_conn.execute(
        sa.text(
            f"INSERT INTO inner_city_zone (name, geom) "
            f"VALUES ('Umweltzone', {_polygon_covering_anchor()})"
        )
    )

    gold.enrich_inside_ring(db_conn)

    inside = db_conn.execute(
        sa.text("SELECT inside_ring FROM listings_geo_context WHERE listing_id = :id"),
        {"id": listing_id},
    ).scalar_one()
    assert inside is True


def test_enrich_inside_ring_false_when_outside(db_conn: sa.Connection) -> None:
    # Listing far from the zone polygon (which still covers the anchor only).
    listing_id = _seed_listing(db_conn, lon=13.7, lat=52.4)
    db_conn.execute(
        sa.text(
            f"INSERT INTO inner_city_zone (name, geom) "
            f"VALUES ('Umweltzone', {_polygon_covering_anchor()})"
        )
    )

    gold.enrich_inside_ring(db_conn)

    inside = db_conn.execute(
        sa.text("SELECT inside_ring FROM listings_geo_context WHERE listing_id = :id"),
        {"id": listing_id},
    ).scalar_one()
    assert inside is False


def test_enrich_noise_writes_lnight_scalar_and_blob(db_conn: sa.Connection) -> None:
    listing_id = _seed_listing(db_conn)
    # A noise sample within the 50 m coverage gate (~7 m east of the anchor).
    db_conn.execute(
        sa.text(
            """
            INSERT INTO strategic_noise_2022
                (noise_total_lden, noise_total_lnight, noise_street_lden,
                 noise_rail_lden, geom)
            VALUES (62.5, 55.0, 60.0, 40.0,
                    ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))
            """
        ),
        {"lon": _LISTING_LON + 0.0001, "lat": _LISTING_LAT},
    )

    gold.enrich_noise(db_conn)

    row = db_conn.execute(
        sa.text(
            "SELECT noise_total_lden, noise_total_lnight, noise_profile "
            "FROM listings_geo_context WHERE listing_id = :id"
        ),
        {"id": listing_id},
    ).one()
    assert row.noise_total_lden == 62.5
    assert row.noise_total_lnight == 55.0
    assert row.noise_profile["total_lnight"] == 55.0
    assert row.noise_profile["total_lden"] == 62.5
