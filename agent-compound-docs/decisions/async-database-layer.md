# Async SQLAlchemy on the backend request path

Decided 2026-06-15 during the search-perf refactor.

## Context

`SearchService.search()` was declared `async def` but called
`self.db.execute(stmt)` synchronously. That blocks the asyncio event
loop during the SQL roundtrip — fine when SQL is fast, awful when one
slow query holds up every other request (and the AG-UI SSE stream).

Same pattern in `chat/service.py:dispatch_agent_request` and
everywhere else in the request path.

## Decision

Move the FastAPI request path to async SQLAlchemy (`postgresql+asyncpg`).
Keep a parallel sync engine for things that genuinely need sync:

- **Alembic migrations** (async support is awkward; sync just works)
- **Direct script entry points** (Python `lifespan` shouldn't have to
  set up an event loop just to query the DB at startup)

Two engines, one declarative `Base`. The observability hooks (per-request
SQL comment tagging + DB-error logging) attach to BOTH engines.

```python
# core/database.py
sync_engine = create_engine(...)              # Alembic, scripts
async_engine = create_async_engine(...)       # FastAPI requests

# Both engines tag SQL with /* session=… run=… */ from contextvars
event.listen(sync_engine, "before_cursor_execute", ...)
event.listen(async_engine.sync_engine, "before_cursor_execute", ...)
```

## Why ETL stays sync

The ingestion service (`services/ingestion/`) is batch. Each ETL is one
process, one transaction, sequential by design (silver → gold → platinum).
There's no concurrency to extract — `await`ing each statement would add
overhead and `asyncio.run()` wrappers for no functional benefit.

The user pinged this question explicitly: should ETL go async too?
Answer: no. Batch jobs don't benefit. ETL keeps using
`services/ingestion/src/db.py` with its sync `engine` + `SessionLocal`.

## Why Alembic stays sync

Alembic has experimental async support but:
- The migration scripts themselves are inherently sequential — async
  doesn't help.
- Adding `asyncio.run` wrappers around `alembic upgrade head` adds
  failure modes for zero gain.
- Alembic's `env.py` async template requires extra moving parts.

Sync engine for migrations is the path of least surprise.

## What changed in code

- `core/database.py` — added `async_engine`, `async_sessionmaker`,
  `get_async_db()`. Existing sync engine kept under same name (renamed
  to `sync_engine` internally for clarity).
- `core/dependencies.py` — `get_search_service` / `get_listing_service`
  now inject `AsyncSession` via `Depends(get_async_db)`.
- `search/service.py`, `chat/service.py`, `listings/service.py` — all
  `async def`, all `await self.db.execute(stmt)`.

## Sources

- [SQLAlchemy async docs](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- [FastAPI async SQL guide](https://fastapi.tiangolo.com/advanced/async-sql-databases/)
- [asyncpg vs psycopg2 perf comparison](https://magicstack.github.io/asyncpg/current/usage.html)
