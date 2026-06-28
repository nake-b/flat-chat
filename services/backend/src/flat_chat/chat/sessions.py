"""SessionStore — durable conversation storage behind a small Protocol.

`InMemorySessionStore` (process-lifetime dict, for tests / fallback) and
`DbSessionStore` (Postgres-backed, the default) both satisfy `SessionStore`.
Call sites depend only on the Protocol.

`create` / `get` / `save` are async (DB I/O); `lock` stays sync — it returns an
async context manager used to serialize concurrent turns on one conversation.

Decision doc: session-persistence.md.
"""

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import UUID, uuid4

from pydantic_ai.messages import ModelMessagesTypeAdapter
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from flat_chat.chat.models import Conversation, Message, SessionStateRow
from flat_chat.chat.session_state import SessionState
from flat_chat.chat.state import ChatSession

logger = logging.getLogger(__name__)


class SessionNotFoundError(KeyError):
    """Raised when a session_id has no matching session in the store."""


class SessionStore(Protocol):
    """Storage for ChatSession objects.

    The shape is intentionally small: create, get, save, lock. `lock` exists so
    concurrent requests on the same session_id can be serialized; both impls use
    an in-process `asyncio.Lock` (single-process MVP). A multi-process deployment
    would swap in a Postgres advisory lock held for the stream's lifetime.
    """

    async def create(self, user_id: str) -> ChatSession: ...

    async def get(self, session_id: str) -> ChatSession: ...

    async def save(self, session: ChatSession) -> None: ...

    def lock(self, session_id: str) -> AbstractAsyncContextManager[object]: ...


class InMemorySessionStore:
    """Process-lifetime dict-backed store. Loses everything on restart.

    Kept for unit tests and as a fallback; `DbSessionStore` is the default.
    """

    _MAX_SESSIONS = 100

    def __init__(self) -> None:
        self._sessions: dict[str, ChatSession] = {}
        # Locks are created lazily for existing sessions only — never indexed
        # by arbitrary IDs, which would let an attacker grow the dict via the
        # lock() call alone.
        self._locks: dict[str, asyncio.Lock] = {}

    async def create(self, user_id: str) -> ChatSession:
        if len(self._sessions) >= self._MAX_SESSIONS:
            oldest_id = min(self._sessions, key=lambda k: self._sessions[k].created_at)
            del self._sessions[oldest_id]
            self._locks.pop(oldest_id, None)
        session = ChatSession(id=str(uuid4()), user_id=user_id)
        self._sessions[session.id] = session
        return session

    async def get(self, session_id: str) -> ChatSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise SessionNotFoundError(session_id) from exc

    async def save(self, session: ChatSession) -> None:
        # In-memory: ChatSession instance is already the canonical reference —
        # mutations made through `get()` are visible without an explicit save.
        # The call is kept on the Protocol so DB-backed impls have a hook.
        self._sessions[session.id] = session

    def lock(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._sessions:
            raise SessionNotFoundError(session_id)
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]


class DbSessionStore:
    """Postgres-backed SessionStore.

    Owns its OWN short-lived sessions via an injected `session_factory` (defaults
    to `AsyncSessionLocal`) — NOT the request-scoped `get_async_db` session, because
    `save()` runs inside `on_complete` at the END of the SSE stream, after the
    request scope is gone. Each method opens `async with self._session_factory()`,
    commits, and closes.

    The injectable factory also lets integration tests bind the store to their
    rollback connection so test writes don't escape the transaction.

    Locks are in-process (single-process MVP). Note that `dispatch_agent_request`
    calls `get()` BEFORE acquiring the lock, so two concurrent turns on one thread
    can race; the `(conversation_id, seq)` unique constraint makes that a loud
    IntegrityError (the losing turn's save aborts; state stays at the prior turn)
    rather than silent duplication. See R1 in session-persistence.md.
    """

    def __init__(
        self,
        session_factory: Callable[[], AsyncSession] | async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory
        self._locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def _parse_id(session_id: str) -> UUID:
        # A malformed thread_id is a missing session, not a 500.
        try:
            return UUID(session_id)
        except (ValueError, AttributeError, TypeError) as exc:
            raise SessionNotFoundError(session_id) from exc

    async def create(self, user_id: str) -> ChatSession:
        conv_id = uuid4()
        user_uuid = UUID(user_id)
        async with self._session_factory() as db, db.begin():
            # The user must already exist (created by registration / `users.seed`);
            # the conversation just references it. We don't fabricate users here —
            # the FK enforces that. Tests create the user row first.
            db.add(Conversation(id=conv_id, user_id=user_uuid))
        return ChatSession(id=str(conv_id), user_id=user_id)

    async def get(self, session_id: str) -> ChatSession:
        conv_uuid = self._parse_id(session_id)
        async with self._session_factory() as db:
            conv = await db.get(Conversation, conv_uuid)
            if conv is None:
                raise SessionNotFoundError(session_id)

            rows = (
                (
                    await db.execute(
                        select(Message.content)
                        .where(Message.conversation_id == conv_uuid)
                        .order_by(Message.seq)
                    )
                )
                .scalars()
                .all()
            )
            history = ModelMessagesTypeAdapter.validate_python(list(rows))

            snap = await db.get(SessionStateRow, conv_uuid)
            state = (
                SessionState.model_validate(snap.snapshot)
                if snap is not None
                else SessionState()
            )

        return ChatSession(
            id=str(conv.id),
            user_id=str(conv.user_id),
            message_history=history,
            state=state,
            created_at=conv.created_at,
        )

    async def save(self, session: ChatSession) -> None:
        conv_uuid = self._parse_id(session.id)
        # Serialize the whole history once; we append the tail (or rewrite).
        # mode="json" makes datetimes/enums in message parts JSON-native so
        # asyncpg's JSONB codec (json.dumps under the hood) can bind them.
        serialized = ModelMessagesTypeAdapter.dump_python(
            list(session.message_history), mode="json"
        )
        snapshot = session.state.model_dump(mode="json")

        async with self._session_factory() as db, db.begin():
            await db.execute(
                update(Conversation)
                .where(Conversation.id == conv_uuid)
                .values(updated_at=func.now())
            )

            existing = await db.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == conv_uuid)
            )
            existing = existing or 0

            # `result.all_messages()` is normally append-only, but history
            # processors / injected system prompts can rewrite the prefix.
            # Guard: if the DB has more rows than the live history (or we
            # otherwise can't trust a clean tail), rewrite from scratch so
            # the DB stays == the live history. Common path just appends.
            #
            # NOTE: this only detects a *shrink*. An equal-length or longer
            # history whose existing prefix was rewritten in place would slip
            # through and append onto stale rows. That can't happen on our
            # paths today (`all_messages()` only ever grows, and reload
            # injection re-prepends history that came from these very rows, so
            # the prefix is byte-identical) — but if a prefix-rewriting history
            # processor is ever added, this guard must compare the boundary row
            # too, not just the count.

            if existing > len(serialized):
                logger.warning(
                    "History shrank (%d → %d); rewriting messages",
                    existing,
                    len(serialized),
                )
                await db.execute(
                    delete(Message).where(Message.conversation_id == conv_uuid)
                )
                start, tail = 0, serialized
            else:
                start, tail = existing, serialized[existing:]

            for offset, content in enumerate(tail):
                db.add(
                    Message(
                        conversation_id=conv_uuid,
                        seq=start + offset,
                        kind=content.get("kind", "")
                        if isinstance(content, dict)
                        else "",
                        content=content,
                    )
                )

            await db.execute(
                pg_insert(SessionStateRow)
                .values(conversation_id=conv_uuid, snapshot=snapshot)
                .on_conflict_do_update(
                    index_elements=[SessionStateRow.conversation_id],
                    set_={"snapshot": snapshot, "updated_at": func.now()},
                )
            )

    def lock(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]
