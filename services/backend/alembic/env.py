"""Alembic env for the backend service — owns the `app` schema ONLY.

The backend is the source of truth for product state (users / sessions /
bookmarks — not built yet). Those tables live in the `app` Postgres schema;
this env tracks them in `app.alembic_version`.

The medallion + geo-context tables (the `world` schema) are owned by the
INGESTION service's Alembic — see services/ingestion/alembic/. The backend
only READS them (read-only ORM in flat_chat.listings.models, all carrying
`{"schema": "world"}`). The `include_name` / `include_object` filters below
keep autogenerate scoped to `app` so it never tries to create or drop the
ingestion-owned `world.*` tables. See schema-ownership-split.md.

`app.*` models live in `flat_chat.users.models` + `flat_chat.chat.models` (imported
below so `Base.metadata` sees them); migration `0001_app_users_sessions` creates
users / conversations / messages / session_state.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import flat_chat.chat.models  # noqa: F401 — registers app.conversations/messages/session_state
import flat_chat.listings.bookmarks  # noqa: F401 — registers app.bookmarks
import flat_chat.listings.models  # noqa: F401 — registers (world-schema) read models
import flat_chat.users.models  # noqa: F401 — registers app.users
from flat_chat.core.config import settings
from flat_chat.core.database import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# This env owns exactly one schema. Tracked separately from world.alembic_version.
VERSION_TABLE_SCHEMA = "app"


def _include_name(name, type_, parent_names):
    """Only reflect the `app` schema (+ the default) — never `world`/`public`."""
    if type_ == "schema":
        return name in (None, "app")
    return True


def _include_object(object, name, type_, reflected, compare_to):
    """Only manage tables in `app`. Excludes the read-only `world.*` models
    that live in `Base.metadata` so autogenerate never emits DDL for them."""
    if type_ == "table":
        return getattr(object, "schema", None) == "app"
    return True


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        include_name=_include_name,
        include_object=_include_object,
        version_table_schema=VERSION_TABLE_SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_name=_include_name,
            include_object=_include_object,
            version_table_schema=VERSION_TABLE_SCHEMA,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
