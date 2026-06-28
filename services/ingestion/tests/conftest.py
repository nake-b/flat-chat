"""Shared pytest fixtures for the ingestion service.

Integration tests opt in via ``TEST_DATABASE_URL`` — the same gate as
``test_alembic_round_trip.py``. When the var is unset, DB-touching tests skip
silently so a bare ``pytest`` works without postgres.

Setup mirrors the round-trip test + the backend conftest:

    docker compose exec postgres createdb -U flat_chat flat_chat_ingest_test
    export TEST_DATABASE_URL=postgresql://flat_chat:flat_chat@localhost:5432/flat_chat_ingest_test
    cd services/ingestion && uv run pytest

The ``world`` schema is brought to ``head`` once per pytest session; each
test gets a connection wrapped in a transaction that ``ROLLBACK``s on exit,
so tests don't see each other's writes and the DB stays pristine.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

TEST_URL = os.environ.get("TEST_DATABASE_URL", "").strip()

DB_REQUIRED = pytest.mark.skipif(
    not TEST_URL,
    reason="TEST_DATABASE_URL not set — see services/ingestion/tests/conftest.py",
)


def _refuse_canonical(url: str) -> None:
    if "flat-chat-db" in url:
        pytest.fail(
            "Refusing to run integration tests against canonical 'flat-chat-db'. "
            "Point TEST_DATABASE_URL at a throwaway DB."
        )


@pytest.fixture(scope="session")
def test_db_url() -> str:
    if not TEST_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    _refuse_canonical(TEST_URL)
    return TEST_URL


@pytest.fixture(scope="session")
def schema_at_head(test_db_url: str) -> None:
    """Bring the `world` schema to head once per session (idempotent).

    Mirrors the real bring-up: bootstrap (extensions in `public` + the
    `world`/`app` schemas) then ingestion `alembic upgrade head`.
    """
    ingestion_root = Path(__file__).resolve().parents[1]

    engine = sa.create_engine(test_db_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS world"))
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS app"))
    engine.dispose()

    os.environ["DATABASE_URL"] = test_db_url  # ingestion env.py reads this
    cfg = Config(str(ingestion_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(ingestion_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", test_db_url)
    command.upgrade(cfg, "head")


@pytest.fixture
def db_conn(test_db_url: str, schema_at_head: None) -> Iterator[sa.Connection]:
    """A connection inside a transaction rolled back at test end.

    `search_path` is set to `world, public` so unqualified table names in the
    enrichers + test SQL resolve to the ingestion-owned schema (matching the
    alembic env + the gold/run engine).
    """
    engine = sa.create_engine(test_db_url)
    conn = engine.connect()
    trans = conn.begin()
    # Inside the transaction so it doesn't autobegin a second one (which
    # would make conn.begin() above raise). Rolled back with `trans`.
    conn.execute(sa.text("SET LOCAL search_path TO world, public"))
    try:
        yield conn
    finally:
        trans.rollback()
        conn.close()
        engine.dispose()
