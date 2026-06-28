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
from flat_chat.chat.schemas import ConversationSummary
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

    async def list_by_user(self, user_id: str) -> list[ConversationSummary]:
        """Return the user's conversations that have at least one message.

        Powers the sidebar list endpoint (`GET /api/conversations`). Empty
        conversations are filtered out so a "+ New chat" click that never
        sends a message doesn't pollute the sidebar.
        """
        ...

    async def set_title_if_unset(self, session_id: str, title: str) -> bool:
        """Atomically set the title only if it's currently NULL.

        Returns True if the row was updated; False if the title was already
        set (idempotent — protects against a double-fire of the background
        title-gen task). Persistence-only operation; does NOT touch any
        in-memory `ChatSession` instances.
        """
        ...

    async def delete_if_owned(self, session_id: str, user_id: str) -> bool:
        """Hard-delete the conversation iff it belongs to `user_id`.

        Returns True when a row was removed; False when it was missing or
        owned by someone else. The `user_id` guard is structural — even a
        guessable conversation UUID can't wipe another user's row.

        Children in `app.messages` and `app.session_state` go with the
        parent via `ON DELETE CASCADE` (declared in the 0001 migration).
        """
        ...

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

    async def list_by_user(self, user_id: str) -> list[ConversationSummary]:
        # No `updated_at` on in-memory `ChatSession` — `created_at` is fine
        # for tests, which don't exercise re-ordering precisely.
        rows = [
            s
            for s in self._sessions.values()
            if s.user_id == user_id and s.message_history
        ]
        rows.sort(key=lambda s: s.created_at, reverse=True)
        return [
            ConversationSummary(
                id=s.id,
                title=s.title,
                created_at=s.created_at,
                updated_at=s.created_at,
            )
            for s in rows
        ]

    async def set_title_if_unset(self, session_id: str, title: str) -> bool:
        session = self._sessions.get(session_id)
        if session is None or session.title is not None:
            return False
        session.title = title
        return True

    async def delete_if_owned(self, session_id: str, user_id: str) -> bool:
        session = self._sessions.get(session_id)
        if session is None or session.user_id != user_id:
            return False
        del self._sessions[session_id]
        # Lock entry follows the session out, mirroring the LRU-eviction path
        # in `create()` — otherwise `_locks` would grow unbounded.
        self._locks.pop(session_id, None)
        return True

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
            # The user must already exist (created by `scripts/seed_users.py`);
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
            title=conv.title,
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

    async def list_by_user(self, user_id: str) -> list[ConversationSummary]:
        user_uuid = UUID(user_id)
        # EXISTS correlated subquery for the "has at least one message" filter:
        # short-circuits on the first matching message, composes with
        # `ix_messages_conversation_seq`, and reads cleaner than INNER JOIN +
        # DISTINCT. Conversations with no messages (a "+ New chat" click that
        # never sent a prompt) stay invisible to the sidebar.
        # `Conversation.id` tie-breaks the sort for deterministic ordering on
        # equal `updated_at`s (matches the project's marker/preview-prefix idiom).
        stmt = (
            select(
                Conversation.id,
                Conversation.title,
                Conversation.created_at,
                Conversation.updated_at,
            )
            .where(Conversation.user_id == user_uuid)
            .where(
                select(1)
                .where(Message.conversation_id == Conversation.id)
                .exists()
            )
            .order_by(Conversation.updated_at.desc(), Conversation.id)
        )
        async with self._session_factory() as db:
            rows = (await db.execute(stmt)).all()
        return [
            ConversationSummary(
                id=str(row.id),
                title=row.title,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    async def set_title_if_unset(self, session_id: str, title: str) -> bool:
        conv_uuid = self._parse_id(session_id)
        # `WHERE title IS NULL` makes this idempotent at the SQL layer — even
        # if two background title-gen tasks race (shouldn't happen given the
        # in-memory `session.title is None` precheck in `chat/service.py`,
        # but a cheap belt-and-braces), only one UPDATE matches a row.
        async with self._session_factory() as db, db.begin():
            result = await db.execute(
                update(Conversation)
                .where(Conversation.id == conv_uuid)
                .where(Conversation.title.is_(None))
                .values(title=title)
            )
        return (result.rowcount or 0) == 1

    async def delete_if_owned(self, session_id: str, user_id: str) -> bool:
        try:
            conv_uuid = self._parse_id(session_id)
        except SessionNotFoundError:
            return False
        user_uuid = UUID(user_id)
        # Structural ownership guard: `WHERE id=? AND user_id=?` makes it
        # impossible for a foreign caller to wipe another user's row even
        # with a guessable UUID. ON DELETE CASCADE on app.messages and
        # app.session_state sweeps the children.
        async with self._session_factory() as db, db.begin():
            result = await db.execute(
                delete(Conversation)
                .where(Conversation.id == conv_uuid)
                .where(Conversation.user_id == user_uuid)
            )
        return (result.rowcount or 0) == 1

    def lock(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]
