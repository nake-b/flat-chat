"""Alembic head → base → head round-trip test.

Catches drift between ``upgrade()`` and ``downgrade()`` on every migration.
Opt-in via ``TEST_DATABASE_URL`` so it never runs against the canonical
``flat-chat-db`` (or against an unsuspecting dev DB on `pytest`).

Setup:
    Create a dedicated empty database (e.g. ``flat_chat_alembic_test``)
    on the local Postgres and export its URL:

        docker compose exec postgres createdb -U flat_chat flat_chat_alembic_test
        export TEST_DATABASE_URL=postgresql://flat_chat:flat_chat@localhost:5432/flat_chat_alembic_test

    Then ``pytest services/backend/tests/test_alembic_round_trip.py``.

Skipped silently if ``TEST_DATABASE_URL`` is unset. Refuses to run if the
URL hostname mentions ``flat-chat-db`` (the canonical tailnet host) — a
``downgrade base`` would drop every table in production.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

# Bump this when a new migration is added; the round-trip should land here.
LATEST_REVISION = "0005_gold_platinum"

_TEST_URL = os.environ.get("TEST_DATABASE_URL", "").strip()


pytestmark = pytest.mark.skipif(
    not _TEST_URL,
    reason="TEST_DATABASE_URL not set — see test docstring for setup",
)


def _refuse_canonical(url: str) -> None:
    # The tailnet sidecar registers the team's canonical DB as `flat-chat-db`.
    # A `downgrade base` against it would drop every silver + chat table for
    # the whole team. Refuse loudly.
    if "flat-chat-db" in url:
        pytest.fail(
            "Refusing to run alembic round-trip against canonical 'flat-chat-db'. "
            "Use a dedicated test database."
        )


def _alembic_config(url: str) -> Config:
    # The Dockerfile WORKDIR is /app where alembic.ini lives. When tests run
    # via `uv run pytest` we're in services/backend, so the ini is one level
    # up from the tests dir. Resolve relative to this file.
    backend_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    # Override the URL so the test never touches the URL from settings.
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _reset_schema(url: str) -> None:
    """Drop everything in ``public`` and re-enable PostGIS for a clean start."""
    engine = sa.create_engine(url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(sa.text("CREATE SCHEMA public"))
        # Migrations 0002+ assume PostGIS is already present; the dev
        # Dockerfile installs the extension. CREATE EXTENSION is idempotent.
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS postgis"))
    engine.dispose()


def _current_revision(url: str) -> str | None:
    engine = sa.create_engine(url)
    with engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT version_num FROM alembic_version LIMIT 1")
        ).fetchone()
    engine.dispose()
    return row[0] if row else None


def _alembic_version_table_exists(url: str) -> bool:
    engine = sa.create_engine(url)
    with engine.connect() as conn:
        exists = conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'alembic_version'"
            )
        ).fetchone()
    engine.dispose()
    return exists is not None


def test_alembic_head_down_head_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    _refuse_canonical(_TEST_URL)
    _reset_schema(_TEST_URL)

    # The project's alembic env.py overrides `sqlalchemy.url` from
    # `settings.database_url`, so setting it via Config alone is clobbered.
    # Belt + suspenders:
    #   1. Override DATABASE_URL env var so any *fresh* read of the Settings
    #      class will see the test URL.
    #   2. Reload the settings module to rebuild the `settings` instance.
    #   3. Patch the attribute directly in case env.py's `settings` reference
    #      was cached before this test ran.
    monkeypatch.setenv("DATABASE_URL", _TEST_URL)
    from flat_chat.core import config as _config_mod  # type: ignore[import-not-found]

    importlib.reload(_config_mod)
    monkeypatch.setattr(_config_mod.settings, "database_url", _TEST_URL)

    cfg = _alembic_config(_TEST_URL)

    # Forward to latest.
    command.upgrade(cfg, "head")
    assert _current_revision(_TEST_URL) == LATEST_REVISION

    # All the way back. After base, alembic_version is typically empty (or
    # the table is dropped depending on the alembic version) — either way
    # there should be no recorded revision.
    command.downgrade(cfg, "base")
    if _alembic_version_table_exists(_TEST_URL):
        assert _current_revision(_TEST_URL) is None

    # Forward again — proves every downgrade() left the DB in a state the
    # next upgrade() can replay over.
    command.upgrade(cfg, "head")
    assert _current_revision(_TEST_URL) == LATEST_REVISION
