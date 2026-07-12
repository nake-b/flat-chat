"""Integration tests for `UsageService` against real Postgres.

Drives the service through a connection bound with
``join_transaction_mode="create_savepoint"`` (same harness as
`test_session_store.py`) so its `async with db.begin()` writes become savepoint
releases and the outer ROLLBACK discards everything.

Covers the §3 budget accounting: `record()` writes a ledger row, `spent_last_24h`
sums `total_tokens` inside the rolling window, rows older than 24h are excluded,
and spend is isolated per user (the property the whole gate depends on).

Gated on ``TEST_DATABASE_URL`` (see tests/README.md).
"""

from __future__ import annotations

import asyncio

import sqlalchemy as sa
from pydantic_ai.usage import RunUsage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from flat_chat.chat.usage import UsageService

from ..conftest import DB_REQUIRED, ensure_app_users

pytestmark = DB_REQUIRED

USER_A = "00000000-0000-0000-0000-0000000000a1"
USER_B = "00000000-0000-0000-0000-0000000000b2"


async def _run(async_url, body):
    engine = create_async_engine(async_url)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            try:
                await ensure_app_users(conn, USER_A, USER_B)
                factory = async_sessionmaker(
                    bind=conn,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                )
                return await body(conn, UsageService(factory))
            finally:
                await trans.rollback()
    finally:
        await engine.dispose()


def _usage(input_tokens: int, output_tokens: int) -> RunUsage:
    # total_tokens (the budget currency) = input + output; cache tokens are
    # stored but excluded from the total by Pydantic AI.
    return RunUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=999,
        cache_write_tokens=7,
        requests=1,
        tool_calls=2,
    )


async def _backdate_row(conn, user_id: str, total: int, hours_ago: int) -> None:
    """Insert a ledger row with a created_at in the past (bypasses record()'s
    now() default) so the rolling-window exclusion can be exercised."""
    await conn.execute(
        sa.text(
            "INSERT INTO app.usage_ledger "
            "(user_id, input_tokens, output_tokens, cache_read_tokens, "
            " cache_write_tokens, total_tokens, requests, tool_calls, created_at) "
            "VALUES (CAST(:u AS uuid), :t, 0, 0, 0, :t, 1, 0, "
            f"        now() - interval '{hours_ago} hours')"
        ),
        {"u": user_id, "t": total},
    )


def test_record_then_spent_sums_window(async_db_url):
    async def body(conn, svc: UsageService):
        assert await svc.spent_last_24h(USER_A) == 0
        await svc.record(USER_A, _usage(100, 50), conversation_id=None)
        await svc.record(USER_A, _usage(200, 25), conversation_id=None)
        # 150 + 225 = 375, cache tokens NOT counted.
        assert await svc.spent_last_24h(USER_A) == 375

    asyncio.run(_run(async_db_url, body))


def test_rows_older_than_window_excluded(async_db_url):
    async def body(conn, svc: UsageService):
        await svc.record(USER_A, _usage(100, 0), conversation_id=None)  # in window
        await _backdate_row(conn, USER_A, total=10_000, hours_ago=25)  # stale
        # Only the fresh 100 counts; the 10k from 25h ago is outside the window.
        assert await svc.spent_last_24h(USER_A) == 100

    asyncio.run(_run(async_db_url, body))


def test_spend_is_per_user(async_db_url):
    async def body(conn, svc: UsageService):
        await svc.record(USER_A, _usage(500, 500), conversation_id=None)
        assert await svc.spent_last_24h(USER_A) == 1000
        assert await svc.spent_last_24h(USER_B) == 0

    asyncio.run(_run(async_db_url, body))
