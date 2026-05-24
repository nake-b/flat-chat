import asyncio
from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import uuid4

from flat_chat.chat.state import ChatSession


class SessionNotFoundError(KeyError):
    """Raised when a session_id has no matching session in the store."""


class SessionStore(Protocol):
    """Storage for ChatSession objects.

    In-memory now, Postgres-backed later — call sites only depend on this
    Protocol. The shape is intentionally small: create, get, save, lock.
    `lock` exists so concurrent requests on the same session_id can be
    serialized; in-memory uses an asyncio.Lock, a DB-backed impl would
    return a context manager wrapping SELECT FOR UPDATE.
    """

    def create(self) -> ChatSession: ...

    def get(self, session_id: str) -> ChatSession: ...

    def save(self, session: ChatSession) -> None: ...

    def lock(self, session_id: str) -> AbstractAsyncContextManager[object]: ...


class InMemorySessionStore:
    """Process-lifetime dict-backed store. Loses everything on restart.

    Fine for the MVP — the Protocol is the bridge to a DB-backed impl.
    """

    _MAX_SESSIONS = 100

    def __init__(self) -> None:
        self._sessions: dict[str, ChatSession] = {}
        # Locks are created lazily for existing sessions only — never indexed
        # by arbitrary IDs, which would let an attacker grow the dict via the
        # lock() call alone.
        self._locks: dict[str, asyncio.Lock] = {}

    def create(self) -> ChatSession:
        if len(self._sessions) >= self._MAX_SESSIONS:
            oldest_id = min(self._sessions, key=lambda k: self._sessions[k].created_at)
            del self._sessions[oldest_id]
            self._locks.pop(oldest_id, None)
        session = ChatSession(id=str(uuid4()))
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> ChatSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise SessionNotFoundError(session_id) from exc

    def save(self, session: ChatSession) -> None:
        # In-memory: ChatSession instance is already the canonical reference
        # mutations made through `get()` are visible without an explicit save.
        # The call is kept on the Protocol so DB-backed impls have a hook.
        self._sessions[session.id] = session

    def lock(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._sessions:
            raise SessionNotFoundError(session_id)
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]
