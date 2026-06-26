# Backend tests

Two tiers, split into subdirectories so the gate is unmistakable:

- **`tests/unit/`** — no DB, no network. Pure functions, mocked services,
  in-memory containers. Runs anywhere with bare `pytest`. Today: health,
  observability filter/SQL-comment, label bucketing, LLM-context prose,
  in-memory session store, agent-tool state mutations.
- **`tests/integration/`** — execute SQL against a real Postgres. Gated
  on the `TEST_DATABASE_URL` env var; **skip silently when unset** so a
  bare `pytest` (no postgres) is still happy. CI sets it: the workflow
  builds the project's `services/postgres/` image (PostGIS + pgvector)
  and runs this tier against it. Today: alembic round-trip, search SQL
  regression, search NULL-handling, ListingService, listings HTTP route.

`tests/fixtures/factories.py` owns the seed helpers (`listing_row`,
`gold_row`) and the async-transaction drivers (`drive_search`,
`with_session`). Integration files import from there — don't re-roll the
harness per file.

## Running

```bash
# Pure-unit tier — no Postgres needed.
cd services/backend
uv run pytest tests/unit/

# Full suite, including integration.
docker compose exec postgres createdb -U flat_chat flat_chat_test || true
export TEST_DATABASE_URL=postgresql://flat_chat:flat_chat@localhost:5432/flat_chat_test
uv run pytest

# Pick one file.
uv run pytest tests/integration/test_search_service.py
```

The fixtures in `conftest.py` apply `alembic upgrade head` once per
session, and each integration test wraps its work in a transaction that
`ROLLBACK`s on exit — tests don't see each other's writes and the DB
stays pristine.

The fixture refuses to run against any URL containing `flat-chat-db`
(the canonical tailnet DB). Always point at a throwaway local DB.

`conftest.py` also sets a sentinel `DATABASE_URL` if one isn't already
in the environment — `Settings` requires it at module-import time, so
this lets pure-unit collection work even when no DB env is configured.

## Why integration, not just unit, for the SQL layer

The bug that motivated `test_search_service.py` (`jsonb ?| jsonb`)
compiled fine in SQLAlchemy — operator validity is checked by Postgres,
not by the ORM. Pure unit tests that only call `stmt.compile()` cannot
catch this class of bug. Every filter type in `SearchService` is
therefore exercised end-to-end: seed a row that should match, run the
search, assert the row came back. If any operator regresses, `await
self.db.execute(stmt)` raises and the test fails immediately.

`test_search_null_geo_fields.py` is the same idea, the other direction:
NULL on a single geo column must DROP the listing (strict `<`/`IN`
against NULL is NULL). Locks the current semantics so a future "treat
NULL as optimistic pass" refactor surfaces immediately.

## Conventions

- Async DB tests use `asyncio.run` rather than `pytest-asyncio` — keeps
  the dev-deps list small. `tests/fixtures/factories.py:with_session` and
  `:drive_search` are the patterns.
- Seed minimum data inside each test, never share state across tests
  via session-scoped seeds. The rollback boundary is per-test.
- New tables → extend `listing_row` / `gold_row` in `factories.py` OR
  write a parallel helper for the new model. Don't seed via raw SQL —
  the ORM insert keeps the test honest about column names.
- For HTTP tests, override `flat_chat.core.database.get_async_db` to
  yield the transaction-scoped session, then drive the app through
  `httpx.ASGITransport`. `tests/integration/test_listings_api.py` is
  the template.
- LLM-facing prose tests (`tests/unit/test_llm_context.py`) use
  inline expected strings — no snapshot library. Prose shape changes in
  this layer affect the prompt cache and tool-call behaviour, so a
  conscious diff review every time is the point.
