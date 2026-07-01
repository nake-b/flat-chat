"""Integration tests for the silver freshness cutoff (`transformer.prune_stale`).

Real-estate ads go stale fast: a flat not re-seen since the cutoff is treated as
gone from the market. `prune_stale` deletes listings scraped before the cutoff
on every silver run — the durable half of the "old flats" cleanup (the other
half is the matching filter in `transform`, which stops stale bronze from being
re-materialized). A one-off `DELETE FROM listings` would not stick because
`transform` reprocesses all of bronze; this regression test pins that the prune
is keyed on the cutoff, honors the env override, and is idempotent.

These run real SQL against Postgres (the class of bug a stmt-compile assertion
would miss). Opt-in via `TEST_DATABASE_URL` so they never touch the canonical
`flat-chat-db`. Setup:

    docker compose exec postgres createdb -U flat_chat flat_chat_test
    export TEST_DATABASE_URL=postgresql://flat_chat:flat_chat@localhost:5432/flat_chat_test
    cd services/ingestion
    uv run pytest tests/integration/test_silver_prune_stale.py

Skipped silently if `TEST_DATABASE_URL` is unset.
"""

from __future__ import annotations

import datetime as dt
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
            "Refusing to run prune tests against canonical 'flat-chat-db'. "
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
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
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


def _seed(
    session: Session,
    *,
    external_id: str,
    scraped_at: datetime,
    source: str = "kleinanzeigen",
) -> None:
    """Insert one minimal listing with a given scrape timestamp."""
    session.execute(
        sa.text(
            """
            INSERT INTO listings (source_name, external_id, title, address,
                                  scraped_at)
            VALUES (:source, :external_id, :title, :address, :scraped_at)
            """
        ),
        {
            "source": source,
            "external_id": external_id,
            "title": f"Flat {external_id}",
            "address": f"Teststr. {external_id}",
            "scraped_at": scraped_at,
        },
    )


def _surviving_external_ids(session: Session) -> set[str]:
    return {r[0] for r in session.execute(sa.text("SELECT external_id FROM listings"))}


_CUTOFF = dt.date(2026, 6, 1)


def test_prunes_rows_before_cutoff_keeps_rest(session: Session) -> None:
    from silver.transformer import prune_stale

    _seed(session, external_id="may", scraped_at=datetime(2026, 5, 20, tzinfo=UTC))
    _seed(session, external_id="apr", scraped_at=datetime(2026, 4, 1, tzinfo=UTC))
    _seed(session, external_id="june", scraped_at=datetime(2026, 6, 15, tzinfo=UTC))
    session.commit()

    removed = prune_stale(session, cutoff=_CUTOFF)

    assert removed == 2
    assert _surviving_external_ids(session) == {"june"}


def test_cutoff_boundary_is_inclusive_of_cutoff_day(session: Session) -> None:
    from silver.transformer import prune_stale

    # A row scraped exactly at the cutoff midnight is fresh (prune deletes
    # strictly-older rows), matching transform's `scraped_at >= cutoff` filter.
    _seed(session, external_id="boundary", scraped_at=datetime(2026, 6, 1, tzinfo=UTC))
    _seed(
        session,
        external_id="just-before",
        scraped_at=datetime(2026, 5, 31, 23, 59, tzinfo=UTC),
    )
    session.commit()

    removed = prune_stale(session, cutoff=_CUTOFF)

    assert removed == 1
    assert _surviving_external_ids(session) == {"boundary"}


def test_idempotent_second_run_removes_nothing(session: Session) -> None:
    from silver.transformer import prune_stale

    _seed(session, external_id="may", scraped_at=datetime(2026, 5, 20, tzinfo=UTC))
    _seed(session, external_id="june", scraped_at=datetime(2026, 6, 15, tzinfo=UTC))
    session.commit()

    assert prune_stale(session, cutoff=_CUTOFF) == 1
    assert prune_stale(session, cutoff=_CUTOFF) == 0
    assert _surviving_external_ids(session) == {"june"}


def test_reads_env_cutoff_when_none(session: Session, monkeypatch) -> None:
    from silver.transformer import prune_stale

    monkeypatch.setenv("SILVER_MIN_SCRAPED_AT", "2026-06-01")
    _seed(session, external_id="may", scraped_at=datetime(2026, 5, 20, tzinfo=UTC))
    _seed(session, external_id="june", scraped_at=datetime(2026, 6, 15, tzinfo=UTC))
    session.commit()

    # No explicit cutoff → reads SILVER_MIN_SCRAPED_AT.
    removed = prune_stale(session)

    assert removed == 1
    assert _surviving_external_ids(session) == {"june"}
