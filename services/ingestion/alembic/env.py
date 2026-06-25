"""Alembic env for the ingestion service — owns the `world` schema.

The ingestion service is the source of truth for all medallion (iron→platinum)
and geo-context tables. They live in the `world` Postgres schema; this env's
migration history (ported from the backend, revision IDs preserved) is the
authoritative DDL.

Two deliberate choices:

  - `target_metadata = None` — migration-only. The medallion DDL is hand-written
    raw SQL (`op.execute("CREATE TABLE …")`), so there is no ORM metadata to
    autogenerate against. The backend keeps read-only ORM classes pointed at
    `world.*` plus a drift test as the cross-service contract.

  - `search_path = world, public` on the migration connection — so the ported
    raw-SQL `CREATE TABLE listings (…)` statements land in `world` without
    per-statement schema qualification, and postgis/vector types still resolve
    from `public` (where the extensions are installed by the postgres bootstrap).

The version table is `world.alembic_version` (the `world` schema must already
exist — created by the postgres bootstrap / scripts/bootstrap-schemas.sh).

See agent-compound-docs/decisions/schema-ownership-split.md.
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config

# Runtime URL comes from the environment (docker compose / local .env), falling
# back to the alembic.ini placeholder.
_db_url = os.environ.get("DATABASE_URL")
if _db_url:
    config.set_main_option("sqlalchemy.url", _db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None

VERSION_TABLE_SCHEMA = "world"
SEARCH_PATH = "world, public"


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        version_table_schema=VERSION_TABLE_SCHEMA,
    )
    with context.begin_transaction():
        context.execute(f"SET search_path TO {SEARCH_PATH}")
        context.run_migrations()


def run_migrations_online() -> None:
    # Set search_path at CONNECT time via libpq options (not an in-band `SET`,
    # which would autobegin a transaction and disrupt Alembic's own transaction
    # control — that silently rolled the DDL back). This resolves unqualified
    # `CREATE TABLE listings (…)` to `world`, keeping `public` for the
    # postgis/vector types and the listings updated_at trigger function.
    connectable = create_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
        connect_args={"options": f"-csearch_path={SEARCH_PATH.replace(' ', '')}"},
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            version_table_schema=VERSION_TABLE_SCHEMA,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
