from fastapi import APIRouter, Depends, HTTPException

from flat_chat.chat.schemas import ConversationResponse, MessageResponse
from flat_chat.chat.sessions import SessionNotFoundError, SessionStore
from flat_chat.chat.state import ChatSession
from flat_chat.core.dependencies import get_session_store

router = APIRouter()


@router.post("", response_model=ConversationResponse)
def create_conversation(store: SessionStore = Depends(get_session_store)):
    """Allocate a new session. The returned id is used as the AG-UI `thread_id`."""
    session = store.create()
    return ConversationResponse(id=session.id, created_at=session.created_at)


@router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
def get_messages(
    conversation_id: str,
    store: SessionStore = Depends(get_session_store),
):
    """History reload — used by the frontend after a page refresh.

    Sending new messages goes through the AG-UI streaming route at /api/agent;
    this endpoint is read-only and only surfaces user + assistant turns.
    """
    try:
        session = store.get(conversation_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc
    return _serialize_history(session)


def _serialize_history(session: ChatSession) -> list[MessageResponse]:
    """Project the persisted ChatMessage history into user-visible messages.

    History holds only user + assistant turns (see `ChatService._persist`).
    IDs are derived from (session, index) so they're stable across GETs and
    the frontend can dedupe.
    """
    return [
        MessageResponse(
            id=f"{session.id}:{idx}",
            role=msg.role,
            content=msg.content,
            created_at=msg.created_at,
        )
        for idx, msg in enumerate(session.message_history)
    ]
