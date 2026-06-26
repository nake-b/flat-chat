"""Integration tests for silver duplicate collapsing (`transformer.deduplicate`).

Companies repost the same flat with a fresh ``external_id`` (often just a new
price), so the ``(source_name, external_id)`` UPSERT key lets each repost in as
its own row. ``deduplicate`` is the second pass that collapses rows sharing a
``(title, address)`` down to one survivor — geocoded-first, then newest.

These run real SQL against Postgres (window function + ``btrim`` +
``location IS NOT NULL``), the class of bug a stmt-compile assertion would miss.

Opt-in via ``TEST_DATABASE_URL`` so it never runs against the canonical
``flat-chat-db``. Setup:

    docker compose exec postgres createdb -U flat_chat flat_chat_test
    export TEST_DATABASE_URL=postgresql://flat_chat:flat_chat@localhost:5432/flat_chat_test
    cd services/ingestion
    uv run pytest tests/integration/test_silver_deduplication.py

Skipped silently if ``TEST_DATABASE_URL`` is unset.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

_TEST_URL = os.environ.get("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _TEST_URL,
    reason="TEST_DATABASE_URL not set — see test docstring for setup",
)


def _refuse_canonical(url: str) -> None:
    if "flat-chat-db" in url:
        pytest.fail(
            "Refusing to run dedup tests against canonical 'flat-chat-db'. "
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
    # search_path=world so unqualified `listings` resolves exactly like db.py.
    eng = sa.create_engine(
        _TEST_URL, connect_args={"options": "-csearch_path=world,public"}
    )
    yield eng
    eng.dispose()


@pytest.fixture()
def session(engine: sa.Engine) -> Session:
    with Session(engine) as s:
        s.execute(sa.text("TRUNCATE world.listings CASCADE"))
        s.commit()
        yield s


_BASE_TS = datetime(2026, 1, 1, tzinfo=UTC)


def _seed(
    session: Session,
    *,
    source: str,
    external_id: str,
    title: str | None,
    address: str | None,
    day: int,
    lat: float | None = None,
    lon: float | None = None,
) -> str:
    """Insert one listing; `day` orders rows by scraped_at (higher = newer)."""
    loc = (
        "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)"
        if lat is not None and lon is not None
        else "NULL"
    )
    sql = sa.text(
        f"""
        INSERT INTO listings (source_name, external_id, title, address,
                              scraped_at, latitude, longitude, location)
        VALUES (:source, :external_id, :title, :address, :scraped_at,
                :lat, :lon, {loc})
        RETURNING id
        """
    )
    return str(
        session.execute(
            sql,
            {
                "source": source,
                "external_id": external_id,
                "title": title,
                "address": address,
                "scraped_at": _BASE_TS.replace(day=day),
                "lat": lat,
                "lon": lon,
            },
        ).scalar()
    )


def _surviving_external_ids(session: Session) -> set[str]:
    return {r[0] for r in session.execute(sa.text("SELECT external_id FROM listings"))}


def test_same_source_same_title_address_collapses_to_newest(session: Session) -> None:
    from silver.transformer import deduplicate

    _seed(
        session,
        source="kleinanzeigen",
        external_id="old",
        title="Helles 2-Zi",
        address="Manteuffelstr. 42",
        day=1,
        lat=52.5,
        lon=13.4,
    )
    _seed(
        session,
        source="kleinanzeigen",
        external_id="new",
        title="Helles 2-Zi",
        address="Manteuffelstr. 42",
        day=5,
        lat=52.5,
        lon=13.4,
    )
    session.commit()

    removed = deduplicate(session)

    assert removed == 1
    assert _surviving_external_ids(session) == {"new"}  # newest scraped_at survives


def test_collapses_across_sources(session: Session) -> None:
    from silver.transformer import deduplicate

    _seed(
        session,
        source="wg-gesucht",
        external_id="wg-1",
        title="Cosy room",
        address="Boxhagener Str. 1",
        day=1,
        lat=52.5,
        lon=13.4,
    )
    _seed(
        session,
        source="kleinanzeigen",
        external_id="ka-1",
        title="Cosy room",
        address="Boxhagener Str. 1",
        day=2,
        lat=52.5,
        lon=13.4,
    )
    session.commit()

    removed = deduplicate(session)

    assert removed == 1
    assert _surviving_external_ids(session) == {"ka-1"}


def test_prefers_geocoded_over_newer_ungeocoded(session: Session) -> None:
    from silver.transformer import deduplicate

    _seed(
        session,
        source="wohninberlin",
        external_id="geo-old",
        title="Neubau",
        address="Bismarckstr. 17",
        day=1,
        lat=52.5,
        lon=13.4,
    )  # older, geocoded
    _seed(
        session,
        source="wohninberlin",
        external_id="nogeo-new",
        title="Neubau",
        address="Bismarckstr. 17",
        day=9,
    )  # newer, no coordinates
    session.commit()

    removed = deduplicate(session)

    assert removed == 1
    assert _surviving_external_ids(session) == {"geo-old"}


def test_null_or_blank_title_or_address_never_collapses(session: Session) -> None:
    from silver.transformer import deduplicate

    # Two NULL-title rows at the same address must NOT be treated as duplicates.
    _seed(
        session,
        source="wg-gesucht",
        external_id="null-1",
        title=None,
        address="Somestr. 1",
        day=1,
    )
    _seed(
        session,
        source="wg-gesucht",
        external_id="null-2",
        title=None,
        address="Somestr. 1",
        day=2,
    )
    # Blank (whitespace-only) title is likewise excluded.
    _seed(
        session,
        source="wg-gesucht",
        external_id="blank-1",
        title="   ",
        address="Otherstr. 2",
        day=1,
    )
    _seed(
        session,
        source="wg-gesucht",
        external_id="blank-2",
        title="   ",
        address="Otherstr. 2",
        day=2,
    )
    session.commit()

    removed = deduplicate(session)

    assert removed == 0
    assert _surviving_external_ids(session) == {
        "null-1",
        "null-2",
        "blank-1",
        "blank-2",
    }


def test_idempotent_second_run_removes_nothing(session: Session) -> None:
    from silver.transformer import deduplicate

    _seed(
        session,
        source="kleinanzeigen",
        external_id="a",
        title="Dup",
        address="Dupstr. 3",
        day=1,
        lat=52.5,
        lon=13.4,
    )
    _seed(
        session,
        source="kleinanzeigen",
        external_id="b",
        title="Dup",
        address="Dupstr. 3",
        day=2,
        lat=52.5,
        lon=13.4,
    )
    session.commit()

    assert deduplicate(session) == 1
    assert deduplicate(session) == 0
