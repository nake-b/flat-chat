"""Database engines and session factories.

Two engines coexist:

  - `sync_engine` / `SessionLocal` — used by Alembic migrations and any
    sync-context code. Driver: `psycopg2`. Stays for migrations because
    Alembic's async support is awkward.

  - `async_engine` / `AsyncSessionLocal` — used by every FastAPI request
    path. Driver: `asyncpg`. Non-blocking event loop is mandatory for
    AG-UI SSE streaming and concurrent agent runs.

Both engines wire the same observability hooks (per-request SQL comment
tagging + DB-error logging through our handler). The contextvars in
`observability.py` are set in `ChatService.dispatch_agent_request`; the
hooks read them so SQL fired from anywhere inside a request carries the
session+run id straight into Postgres for debugging stuck queries.

Architecture-decision doc: `agent-compound-docs/decisions/async-database-layer.md`
"""

import logging

from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from flat_chat.core.config import settings
from flat_chat.core.observability import run_id_var, session_id_var

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sync engine — Alembic, scripts, and anything that needs the sync API.
# ---------------------------------------------------------------------------

sync_engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=sync_engine)


# ---------------------------------------------------------------------------
# Async engine — FastAPI request path. Driver swap: postgresql+psycopg2 →
# postgresql+asyncpg. Same connection string, different scheme.
# ---------------------------------------------------------------------------

_async_url = settings.database_url.replace(
    "postgresql+psycopg2://", "postgresql+asyncpg://", 1
).replace("postgresql://", "postgresql+asyncpg://", 1)

async_engine = create_async_engine(_async_url, pool_pre_ping=True)
AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=async_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Observability hooks — wired to BOTH engines so any SQL fired during a
# request carries the session/run id, whether the path is sync or async.
# ---------------------------------------------------------------------------


def _inject_request_context_comment(
    conn, cursor, statement, params, context, executemany
):
    """Prepend `/* session=… run=… */` to every SQL statement.

    The values come from the ContextVars set by `ChatService.dispatch_agent_request`,
    so any query fired within that asyncio task carries the calling
    conversation/turn ids straight into Postgres. A stalled row in
    `pg_stat_activity` then tells you which session/run is responsible:

        SELECT pid, now() - query_start AS age, query
        FROM pg_stat_activity
        WHERE state = 'active' AND query LIKE '%session=…%';

    No-op outside a request (startup, background) — neither var is set, so
    the comment is omitted and the SQL goes through unchanged.
    """
    parts: list[str] = []
    if sid := session_id_var.get():
        parts.append(f"session={sid}")
    if rid := run_id_var.get():
        parts.append(f"run={rid}")
    if not parts:
        return statement, params
    return f"/* {' '.join(parts)} */ {statement}", params


def _log_dbapi_error(ctx):
    """Emit every DBAPI error through our logger with the request context."""
    logger.exception(
        "DB error: %s",
        ctx.original_exception,
        exc_info=ctx.original_exception,
    )


# Register hooks on the sync engine directly.
event.listen(
    sync_engine, "before_cursor_execute", _inject_request_context_comment, retval=True
)
event.listen(sync_engine, "handle_error", _log_dbapi_error)

# For async engines, hooks attach to the underlying sync engine
# (SQLAlchemy's async layer wraps sync DBAPI under the hood). `sync_engine`
# attribute on the async engine surfaces that.
event.listen(
    async_engine.sync_engine,
    "before_cursor_execute",
    _inject_request_context_comment,
    retval=True,
)
event.listen(async_engine.sync_engine, "handle_error", _log_dbapi_error)


# ---------------------------------------------------------------------------
# FastAPI dependency entry points
# ---------------------------------------------------------------------------


def get_db():
    """Sync session for any sync-context code paths."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_async_db():
    """Async session for FastAPI route handlers and async services."""
    async with AsyncSessionLocal() as session:
        yield session
