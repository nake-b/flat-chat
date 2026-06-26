"""Alembic head → base → head round-trip for the ingestion-owned `world` schema.

The medallion + geo-context migrations live here now (ported from the backend,
revision IDs preserved). This catches drift between every ``upgrade()`` and
``downgrade()``.

Opt-in via ``TEST_DATABASE_URL`` so it never runs against the canonical
``flat-chat-db``. Setup:

    docker compose exec postgres createdb -U flat_chat flat_chat_alembic_test
    export TEST_DATABASE_URL=postgresql://flat_chat:flat_chat@localhost:5432/flat_chat_alembic_test
    cd services/ingestion && uv run pytest tests/integration/test_alembic_round_trip.py

Skipped silently if ``TEST_DATABASE_URL`` is unset.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

# Bump when a new world migration is added; the round-trip should land here.
LATEST_REVISION = "0007_geo_context_v2"

_TEST_URL = os.environ.get("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _TEST_URL,
    reason="TEST_DATABASE_URL not set — see test docstring for setup",
)


def _refuse_canonical(url: str) -> None:
    if "flat-chat-db" in url:
        pytest.fail(
            "Refusing to run alembic round-trip against canonical 'flat-chat-db'. "
            "Use a dedicated test database."
        )


def _alembic_config(url: str) -> Config:
    ingestion_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(ingestion_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(ingestion_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _bootstrap(url: str) -> None:
    """Reset to the bootstrapped pre-migration state: clean schemas + the
    DB-global extensions the postgres init SQL would create on a fresh volume."""
    engine = sa.create_engine(url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(sa.text("DROP SCHEMA IF EXISTS world CASCADE"))
        conn.execute(sa.text("DROP SCHEMA IF EXISTS app CASCADE"))
        conn.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(sa.text("CREATE SCHEMA public"))
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        # pg_trgm backs the named_places trigram indexes the 0007 migration
        # creates; the postgres bootstrap installs it on a fresh volume.
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        conn.execute(sa.text("CREATE SCHEMA world"))
        conn.execute(sa.text("CREATE SCHEMA app"))
    engine.dispose()


def _current_revision(url: str) -> str | None:
    engine = sa.create_engine(url)
    with engine.connect() as conn:
        present = conn.execute(
            sa.text("SELECT to_regclass('world.alembic_version')")
        ).scalar()
        if present is None:
            engine.dispose()
            return None
        row = conn.execute(
            sa.text("SELECT version_num FROM world.alembic_version LIMIT 1")
        ).fetchone()
    engine.dispose()
    return row[0] if row else None


def test_world_alembic_head_down_head_round_trip() -> None:
    _refuse_canonical(_TEST_URL)
    os.environ["DATABASE_URL"] = _TEST_URL  # ingestion env.py reads this
    _bootstrap(_TEST_URL)

    cfg = _alembic_config(_TEST_URL)

    command.upgrade(cfg, "head")
    assert _current_revision(_TEST_URL) == LATEST_REVISION

    command.downgrade(cfg, "base")
    assert _current_revision(_TEST_URL) is None

    command.upgrade(cfg, "head")
    assert _current_revision(_TEST_URL) == LATEST_REVISION


def test_world_tables_created_in_world_schema() -> None:
    """The ported raw-SQL migrations must land tables in `world`, not `public`."""
    _refuse_canonical(_TEST_URL)
    os.environ["DATABASE_URL"] = _TEST_URL
    _bootstrap(_TEST_URL)

    cfg = _alembic_config(_TEST_URL)
    command.upgrade(cfg, "head")

    engine = sa.create_engine(_TEST_URL)
    with engine.connect() as conn:
        for tbl in ("listings", "listings_geo_context", "listings_nearby_transit"):
            assert conn.execute(
                sa.text(f"SELECT to_regclass('world.{tbl}')")
            ).scalar() is not None, f"world.{tbl} missing"
            # And NOT accidentally in public.
            assert conn.execute(
                sa.text(f"SELECT to_regclass('public.{tbl}')")
            ).scalar() is None, f"{tbl} leaked into public"
    engine.dispose()
