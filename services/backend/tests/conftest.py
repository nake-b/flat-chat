"""Shared pytest fixtures.

Tests that touch the database opt in via ``TEST_DATABASE_URL`` — same
gate as ``test_alembic_round_trip.py``. When the var is unset, those
tests skip silently so a bare ``pytest`` works in CI without postgres.

Setup mirrors the round-trip test:

    docker compose exec postgres createdb -U flat_chat flat_chat_test
    export TEST_DATABASE_URL=postgresql://flat_chat:flat_chat@localhost:5432/flat_chat_test
    pytest services/backend/tests

Schema is brought to ``head`` once per pytest session; each test gets a
fresh connection wrapped in a transaction that ``ROLLBACK``s on exit, so
tests don't see each other's writes and leave the DB pristine.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

# ``flat_chat.core.config.Settings`` requires DATABASE_URL at module import
# time (it's a required field). Pure-unit tests don't need a real DB but they
# do need the module to import — set a sentinel so collection works even
# without env setup. Integration tests overwrite this via ``TEST_DATABASE_URL``.
os.environ.setdefault("DATABASE_URL", "postgresql://unset:unset@unset/unset")
# `jwt_secret` is a required Settings field (no insecure default ships). Tests
# never verify real tokens against this, so a fixed sentinel is fine.
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-prod")

TEST_URL = os.environ.get("TEST_DATABASE_URL", "").strip()


DB_REQUIRED = pytest.mark.skipif(
    not TEST_URL,
    reason="TEST_DATABASE_URL not set — see services/backend/tests/README.md",
)


def _refuse_canonical(url: str) -> None:
    # The tailnet sidecar registers the team's canonical DB as
    # ``flat-chat-db``. Refuse loudly — a misconfigured TEST_DATABASE_URL
    # pointing at it would seed and roll back against shared dev data.
    if "flat-chat-db" in url:
        pytest.fail(
            "Refusing to run integration tests against canonical 'flat-chat-db'. "
            "Point TEST_DATABASE_URL at a throwaway DB."
        )


def _async_url(sync_url: str) -> str:
    return sync_url.replace(
        "postgresql+psycopg2://", "postgresql+asyncpg://", 1
    ).replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture(scope="session")
def test_db_url() -> str:
    if not TEST_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    _refuse_canonical(TEST_URL)
    return TEST_URL


@pytest.fixture(scope="session")
def schema_at_head(test_db_url: str) -> None:
    """Build the full schema on the test DB once per session.

    After the schema-ownership split the backend's own Alembic only owns the
    (currently empty) ``app`` schema, so the tables the backend READS live in
    ``world`` and are created by the INGESTION service's Alembic. Mirror the
    real bring-up order here:

      1. bootstrap — extensions (in ``public``) + ``world`` / ``app`` schemas
         (idempotent; mirrors services/postgres/init/01-bootstrap.sql)
      2. ingestion ``alembic upgrade head`` → creates ``world.*``
      3. backend ``alembic upgrade head`` → ``app.*`` (no-op today; boundary-only)

    All steps are idempotent so a persistent test DB can be reused across runs.
    """
    import sqlalchemy as sa

    from flat_chat.core.config import settings

    backend_root = Path(__file__).resolve().parents[1]
    services_root = backend_root.parent
    ingestion_root = services_root / "ingestion"

    # 1. Bootstrap.
    engine = sa.create_engine(test_db_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS world"))
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS app"))
    engine.dispose()

    # 2. ingestion alembic → world.* (its env.py reads DATABASE_URL).
    os.environ["DATABASE_URL"] = test_db_url
    ing_cfg = Config(str(ingestion_root / "alembic.ini"))
    ing_cfg.set_main_option("script_location", str(ingestion_root / "alembic"))
    ing_cfg.set_main_option("sqlalchemy.url", test_db_url)
    command.upgrade(ing_cfg, "head")

    # 3. backend alembic → app.* (no-op until the first app migration lands).
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", test_db_url)
    original_url = settings.database_url
    settings.database_url = test_db_url
    try:
        command.upgrade(cfg, "head")
    finally:
        settings.database_url = original_url


@pytest.fixture
def async_db_url(test_db_url: str, schema_at_head: None) -> str:
    """asyncpg-flavoured URL for AsyncEngine."""
    return _async_url(test_db_url)


async def ensure_app_users(conn, *user_ids: str) -> None:
    """Insert real `app.users` rows for the given ids (idempotent).

    `email` / `hashed_password` are NOT NULL and `DbSessionStore.create` no longer
    fabricates users, so any test that creates a conversation for a fixed user id
    must materialize that user first. Synthetic but valid values — the DB only
    enforces NOT NULL + unique email, not real hashes. Runs on the test's outer
    connection so it rolls back with everything else.
    """
    import sqlalchemy as sa

    for uid in user_ids:
        await conn.execute(
            sa.text(
                "INSERT INTO app.users (id, email, hashed_password) "
                "VALUES (CAST(:id AS uuid), :email, '!') "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": uid, "email": f"user-{uid}@flat-chat.dev"},
        )
