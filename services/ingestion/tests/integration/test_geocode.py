"""Integration tests for the silver geocoding pass (`transformer._geocode_missing`).

These run real SQL against Postgres — the `UPDATE ... ST_SetSRID(ST_MakePoint(...))`
write is exactly the PostGIS-shape that compiles in SQLAlchemy but only Postgres
can accept or reject. The geocoder itself is a stub (no network): we inject a
fake into `_geocode_missing`, so these tests assert the DB read/write + the
Berlin-validation + idempotency, not Nominatim.

Opt-in via ``TEST_DATABASE_URL`` so it never runs against the canonical
``flat-chat-db``. Setup:

    docker compose exec postgres createdb -U flat_chat flat_chat_test
    export TEST_DATABASE_URL=postgresql://flat_chat:flat_chat@localhost:5432/flat_chat_test
    cd services/ingestion
    uv run pytest tests/integration/test_geocode.py

Skipped silently if ``TEST_DATABASE_URL`` is unset.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

_TEST_URL = os.environ.get("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _TEST_URL,
    reason="TEST_DATABASE_URL not set — see test docstring for setup",
)


def _refuse_canonical(url: str) -> None:
    if "flat-chat-db" in url:
        pytest.fail(
            "Refusing to run geocode tests against canonical 'flat-chat-db'. "
            "Use a dedicated test database."
        )


def _bootstrap(url: str) -> None:
    """Clean schemas + the DB-global extensions the postgres init SQL creates."""
    engine = sa.create_engine(url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(sa.text("DROP SCHEMA IF EXISTS world CASCADE"))
        conn.execute(sa.text("DROP SCHEMA IF EXISTS app CASCADE"))
        conn.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(sa.text("CREATE SCHEMA public"))
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(sa.text("CREATE SCHEMA world"))
        conn.execute(sa.text("CREATE SCHEMA app"))
    engine.dispose()


def _alembic_config(url: str) -> Config:
    ingestion_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(ingestion_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(ingestion_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture(scope="module")
def engine() -> sa.Engine:
    _refuse_canonical(_TEST_URL)
    os.environ["DATABASE_URL"] = _TEST_URL  # ingestion env.py + config read this
    _bootstrap(_TEST_URL)
    command.upgrade(_alembic_config(_TEST_URL), "head")
    eng = sa.create_engine(
        _TEST_URL, connect_args={"options": "-csearch_path=world,public"}
    )
    yield eng
    eng.dispose()


@pytest.fixture()
def conn(engine: sa.Engine):
    with engine.connect() as c:
        c.execute(sa.text("TRUNCATE world.listings CASCADE"))
        c.commit()
        yield c


class _StubGeocoder:
    """Duck-typed stand-in for NominatimGeocoder — returns a canned point."""

    def __init__(self, result: tuple[float, float] | None):
        self.result = result
        self.queries: list[str] = []

    def geocode(self, query: str) -> tuple[float, float] | None:
        self.queries.append(query)
        return self.result


def _insert(
    conn,
    *,
    external_id: str,
    address: str | None = None,
    postal_code: str | None = None,
    district: str | None = None,
    city: str | None = None,
) -> str:
    """Insert a coordinate-less listing (location/lat/lon all NULL)."""
    row = conn.execute(
        sa.text(
            """
            INSERT INTO listings (source_name, external_id, address,
                                  postal_code, district, city, scraped_at)
            VALUES ('wohninberlin', :e, :addr, :pc, :d, :c, now())
            RETURNING id::text
            """
        ),
        {
            "e": external_id,
            "addr": address,
            "pc": postal_code,
            "d": district,
            "c": city,
        },
    ).scalar()
    conn.commit()
    return row


def _insert_with_location_only(
    conn, *, external_id: str, lat: float, lon: float, address: str | None = None
) -> str:
    """Insert a listing carrying a `location` geometry but NULL scalar coords —
    the inconsistent state a backfill must reconcile."""
    row = conn.execute(
        sa.text(
            """
            INSERT INTO listings (source_name, external_id, scraped_at, address,
                                  location)
            VALUES ('wohninberlin', :e, now(), :addr,
                    ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))
            RETURNING id::text
            """
        ),
        {"e": external_id, "lat": lat, "lon": lon, "addr": address},
    ).scalar()
    conn.commit()
    return row


def _point(conn, listing_id: str):
    return conn.execute(
        sa.text(
            """
            SELECT latitude, longitude,
                   location IS NOT NULL AS has_loc,
                   ST_X(location) AS x, ST_Y(location) AS y
            FROM listings WHERE id = :id
            """
        ),
        {"id": listing_id},
    ).one()


def test_geocodes_coordinate_less_listing_and_writes_point(conn):
    from silver.transformer import _geocode_missing

    lid = _insert(
        conn, external_id="wib-1", address="Karl-Marx-Allee 90", postal_code="10243"
    )

    geocoded, failed, skipped = _geocode_missing(conn, _StubGeocoder((52.52, 13.405)))

    assert (geocoded, failed, skipped) == (1, 0, 0)
    row = _point(conn, lid)
    assert row.latitude == 52.52 and row.longitude == 13.405
    assert row.has_loc is True
    # location must be the matching PostGIS point (lon=X, lat=Y).
    assert round(row.x, 5) == 13.405 and round(row.y, 5) == 52.52


def test_skips_listing_with_no_address(conn):
    from silver.transformer import _geocode_missing

    lid = _insert(conn, external_id="wib-noaddr")  # all address fields NULL

    geocoded, failed, skipped = _geocode_missing(conn, _StubGeocoder((52.52, 13.405)))

    assert (geocoded, failed, skipped) == (0, 0, 1)
    assert _point(conn, lid).has_loc is False  # untouched


def test_rejects_out_of_berlin_result(conn):
    from silver.transformer import _geocode_missing

    lid = _insert(conn, external_id="wib-far", address="Somewhere far")

    # Paris-ish point — clean_berlin_coords must reject it.
    geocoded, failed, skipped = _geocode_missing(conn, _StubGeocoder((48.85, 2.35)))

    assert (geocoded, failed, skipped) == (0, 1, 0)
    assert _point(conn, lid).has_loc is False  # not written


def test_idempotent_second_run_geocodes_nothing(conn):
    from silver.transformer import _geocode_missing

    _insert(
        conn, external_id="wib-2", address="Frankfurter Allee 1", postal_code="10247"
    )

    assert _geocode_missing(conn, _StubGeocoder((52.515, 13.475)))[0] == 1
    # Second run: the row now has a location, so it's not selected.
    assert _geocode_missing(conn, _StubGeocoder((52.515, 13.475))) == (0, 0, 0)


def test_limit_caps_rows_processed(conn):
    from silver.transformer import _geocode_missing

    for i in range(3):
        _insert(
            conn,
            external_id=f"wib-lim-{i}",
            address=f"Teststr. {i}",
            postal_code="10115",
        )

    geocoded, _, _ = _geocode_missing(conn, _StubGeocoder((52.53, 13.40)), limit=2)
    assert geocoded == 2
    # One still missing a point.
    remaining = conn.execute(
        sa.text("SELECT count(*) FROM listings WHERE location IS NULL")
    ).scalar()
    assert remaining == 1


# ---------------------------------------------------------------------------
# _fill_latlon_from_location — distribute an existing geometry into the scalars
# ---------------------------------------------------------------------------


def test_fill_latlon_backfills_scalars_from_location(conn):
    from silver.transformer import _fill_latlon_from_location

    lid = _insert_with_location_only(
        conn, external_id="loc-only", lat=52.52, lon=13.405
    )

    assert _fill_latlon_from_location(conn) == 1
    row = _point(conn, lid)
    assert round(row.latitude, 5) == 52.52 and round(row.longitude, 5) == 13.405


def test_fill_latlon_skips_out_of_berlin_location(conn):
    from silver.transformer import _fill_latlon_from_location

    lid = _insert_with_location_only(conn, external_id="loc-far", lat=48.85, lon=2.35)

    assert _fill_latlon_from_location(conn) == 0
    assert _point(conn, lid).latitude is None  # geometry stays, scalars untouched


def test_fill_latlon_idempotent(conn):
    from silver.transformer import _fill_latlon_from_location

    _insert_with_location_only(conn, external_id="loc-2", lat=52.50, lon=13.40)

    assert _fill_latlon_from_location(conn) == 1
    assert _fill_latlon_from_location(conn) == 0  # both scalars set → no match


# ---------------------------------------------------------------------------
# _reset_invalid_location — clear out-of-Berlin / null-island stored points
# ---------------------------------------------------------------------------


def test_reset_invalid_location_clears_null_island(conn):
    from silver.transformer import _reset_invalid_location

    lid = _insert_with_location_only(conn, external_id="zero", lat=0.0, lon=0.0)

    assert _reset_invalid_location(conn) == 1
    row = _point(conn, lid)
    assert row.has_loc is False and row.latitude is None and row.longitude is None


def test_reset_invalid_location_keeps_valid_berlin_point(conn):
    from silver.transformer import _reset_invalid_location

    lid = _insert_with_location_only(conn, external_id="good", lat=52.5, lon=13.4)

    assert _reset_invalid_location(conn) == 0
    assert _point(conn, lid).has_loc is True


def test_reset_then_geocode_heals_bad_location(conn):
    from silver.transformer import _geocode_missing, _reset_invalid_location

    lid = _insert_with_location_only(
        conn, external_id="heal", lat=0.0, lon=0.0, address="Alexanderplatz"
    )

    assert _reset_invalid_location(conn) == 1  # bad point → NULL
    geocoded, _, _ = _geocode_missing(conn, _StubGeocoder((52.521, 13.413)))
    assert geocoded == 1
    row = _point(conn, lid)
    assert row.has_loc is True and round(row.latitude, 3) == 52.521
