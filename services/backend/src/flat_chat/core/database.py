import logging

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from flat_chat.core.config import settings
from flat_chat.core.observability import run_id_var, session_id_var

logger = logging.getLogger(__name__)

# pool_pre_ping=True issues a cheap SELECT 1 before every checkout. Dead
# pool entries (e.g. after a postgres restart) are dropped and replaced
# transparently instead of raising `server closed the connection unexpectedly`
# on the next query.
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


@event.listens_for(engine, "before_cursor_execute", retval=True)
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


@event.listens_for(engine, "handle_error")
def _log_dbapi_error(ctx):
    """Emit every DBAPI error through our logger with the request context.

    Without this, SQLAlchemy / psycopg2 errors only surface via the
    exception's eventual re-raise path — which Pydantic AI catches inside
    the tool wrapper, swallowing the trace from `docker compose logs`. The
    [session=… run=…] prefix comes from the contextvar filter, so this
    lands directly under the failing turn in the log stream.
    """
    logger.exception(
        "DB error: %s",
        ctx.original_exception,
        exc_info=ctx.original_exception,
    )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
