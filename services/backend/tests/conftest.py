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
    """Bring the test DB to alembic head once per session.

    ``alembic/env.py`` clobbers ``sqlalchemy.url`` with ``settings.database_url``
    so we patch the loaded Settings field for the duration of the upgrade.
    Restored afterwards so app code still sees the real DB.
    """
    from flat_chat.core.config import settings

    backend_root = Path(__file__).resolve().parents[1]
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
