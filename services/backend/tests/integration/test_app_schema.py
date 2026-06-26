"""Drift guard for the backend-owned `app` schema.

With the schema brought to head (the `schema_at_head` fixture runs the backend
Alembic), autogenerate must produce NO operations for `app` — i.e. the ORM
models (`users.models` + `chat.models`) exactly match migration
`0001_app_users_sessions`. This is the guard that makes "pure-schema migration
== ORM" a tested invariant rather than an aspiration; it catches a column added
to a model but not to a migration (or vice-versa).

Non-destructive (a metadata-vs-live comparison, no DROP), so it coexists with
the other integration tests sharing the session-scoped schema. Mirrors the
filters in alembic/env.py so only `app` is considered. Gated on
``TEST_DATABASE_URL``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

# Importing the models registers the app tables on Base.metadata.
import flat_chat.chat.models  # noqa: F401
import flat_chat.users.models  # noqa: F401
from flat_chat.core.database import Base

from ..conftest import DB_REQUIRED

pytestmark = DB_REQUIRED


def _include_name(name, type_, parent_names):
    if type_ == "schema":
        return name in (None, "app")
    return True


def _include_object(object_, name, type_, reflected, compare_to):
    if type_ == "table":
        return getattr(object_, "schema", None) == "app"
    return True


def test_app_autogenerate_is_empty(test_db_url, schema_at_head):
    """No pending migration ops for `app` → models and migration agree."""
    engine = sa.create_engine(test_db_url)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(
                conn,
                opts={
                    "include_schemas": True,
                    "include_name": _include_name,
                    "include_object": _include_object,
                    "version_table_schema": "app",
                    "target_metadata": Base.metadata,
                },
            )
            diffs = compare_metadata(ctx, Base.metadata)
    finally:
        engine.dispose()

    assert diffs == [], f"Unexpected app-schema drift: {diffs}"
