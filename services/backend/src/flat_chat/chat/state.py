"""Chat-domain pure data containers.

`ChatSession` (one conversation thread) and `ChatDeps` (per-request
bridge between FastAPI and the agent backend) live here. They hold
references and nothing else — no formatting, no prose composition.

The SessionState shape (used by both the frontend AG-UI mirror and your
agent) lives in `chat/session_state.py`. The agent seam itself is in
`chat/backend.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from flat_chat.chat.session_state import SessionState

if TYPE_CHECKING:
    from flat_chat.listings.service import ListingService
    from flat_chat.search.service import SearchService


@dataclass
class ChatMessage:
    """One user-visible turn, persisted for history reload.

    Framework-neutral on purpose: a `{role, content}` pair plus a timestamp,
    independent of whatever agent framework produced it. `ChatService`
    rebuilds the list from the AG-UI thread on each turn (the frontend sends
    the full history) and appends the assistant's reply.
    """

    role: str
    content: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class ChatSession:
    """One user conversation thread.

    Owns the message history (for the GET history-reload endpoint) and the
    SessionState (canonical in-memory representation of the active
    conversation — what the frontend mirrors). Lives in a SessionStore so
    the storage backend (in-memory now, Postgres later) can swap without
    touching anything else.
    """

    id: str
    message_history: list[ChatMessage] = field(default_factory=list)
    state: SessionState = field(default_factory=SessionState)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class ChatDeps:
    """Per-request deps handed to your `AgentBackend.run`.

    Bridges request-scoped services (`search_service`, `listing_service`)
    with the session-scoped `state`. Your backend mutates `state` in place
    and emits a `StateSnapshotEvent` carrying it; the frontend re-renders
    map markers + cards from those fields.

    The two methods you'll lean on:
      - `search_service.search(params) -> (list[UiApartment], total)`
      - `listing_service.get(id) -> ListingDetail | None`
    """

    search_service: SearchService
    listing_service: ListingService
    session: ChatSession
    # Overwritten per-request by `ChatService` from session.state + the
    # incoming AG-UI envelope's state (frontend-driven changes like
    # `active_id`). The default_factory only matters when constructing deps
    # outside the request path (e.g. unit tests).
    state: SessionState = field(default_factory=SessionState)
