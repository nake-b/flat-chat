"""Per-user LLM token accounting — the §3 budget from llm-rate-limit.md.

`UsageService` is the store + reader for `app.usage_ledger`. Two operations:

  - `spent_last_24h(user_id)` — the pre-run gate reads this in
    `ChatService.dispatch_agent_request` to reject a caller who's over budget
    BEFORE a token is spent (a clean 429, not a truncated mid-stream abort).
  - `record(user_id, usage, conversation_id)` — the accounting hook writes one
    ledger row in `on_complete` at SSE-stream end, from `result.usage`.

Like `DbSessionStore`, it owns its OWN short-lived sessions via an injected
`session_factory` (defaults to `AsyncSessionLocal`) — `record()` runs after the
request scope is gone, so it cannot lean on the request-scoped DB session. The
injectable factory also lets integration tests bind it to their rollback
connection.

Budget currency is `RunUsage.total_tokens` (input + output; Pydantic AI excludes
cache reads/writes), the SAME currency as the per-run `total_tokens_limit`
backstop in `chat/service.py`, so the gate and the backstop agree. See
llm-rate-limit.md.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from uuid import UUID

from pydantic_ai.usage import RunUsage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from flat_chat.chat.models import UsageLedger

logger = logging.getLogger(__name__)

_WINDOW = timedelta(hours=24)


class QuotaExceededError(Exception):
    """The caller has spent their windowed LLM token budget.

    Raised by the pre-run gate; mapped to HTTP 429 at `api/agent.py`. Carrying
    it as a domain exception (not an HTTPException) keeps `ChatService` free of
    framework types — same pattern as `SessionNotFoundError`.
    """


class UsageService:
    def __init__(
        self,
        session_factory: Callable[[], AsyncSession] | async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def spent_last_24h(self, user_id: str) -> int:
        """Sum of `total_tokens` this user spent in the rolling 24h window.

        `now() - interval` is evaluated in Postgres (server clock), so it can't
        drift from an app-side clock. `coalesce(..., 0)` turns the no-rows NULL
        into 0 so the gate arithmetic (`budget - spent`) is always numeric.
        """
        user_uuid = UUID(user_id)
        stmt = (
            select(func.coalesce(func.sum(UsageLedger.total_tokens), 0))
            .where(UsageLedger.user_id == user_uuid)
            .where(UsageLedger.created_at >= func.now() - _WINDOW)
        )
        async with self._session_factory() as db:
            return int((await db.execute(stmt)).scalar_one())

    async def record(
        self, user_id: str, usage: RunUsage, conversation_id: str | None = None
    ) -> None:
        """Append one ledger row for a completed run.

        Best-effort: called from `on_complete`, so a failure here must never
        break the user's turn (the reply already streamed). We log and swallow —
        the worst case is one turn's tokens going unaccounted, not a 500.
        """
        try:
            async with self._session_factory() as db, db.begin():
                db.add(
                    UsageLedger(
                        user_id=UUID(user_id),
                        conversation_id=(
                            UUID(conversation_id) if conversation_id else None
                        ),
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        cache_read_tokens=usage.cache_read_tokens,
                        cache_write_tokens=usage.cache_write_tokens,
                        total_tokens=usage.total_tokens,
                        requests=usage.requests,
                        tool_calls=usage.tool_calls,
                    )
                )
        except Exception:
            logger.exception("Failed to record usage for user %s", user_id)
