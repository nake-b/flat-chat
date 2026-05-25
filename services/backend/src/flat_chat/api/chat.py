from fastapi import APIRouter, Depends, HTTPException
from pydantic_ai.messages import TextPart, UserPromptPart

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
    """Project the agent's ModelMessage history into user-visible messages.

    Only UserPromptPart (→ "user") and TextPart (→ "assistant") are surfaced;
    tool calls, tool returns, system prompts, retries, and thinking parts
    stay internal. IDs are derived from (session, message_idx, part_idx) so
    they're stable across GETs and the frontend can dedupe. Timestamps come
    from the ModelMessage itself when available.
    """
    out: list[MessageResponse] = []
    for msg_idx, msg in enumerate(session.message_history):
        ts = getattr(msg, "timestamp", None) or session.created_at
        for part_idx, part in enumerate(msg.parts):
            if isinstance(part, UserPromptPart):
                role = "user"
            elif isinstance(part, TextPart):
                role = "assistant"
            else:
                continue
            content = getattr(part, "content", None)
            if not isinstance(content, str):
                continue
            out.append(
                MessageResponse(
                    id=f"{session.id}:{msg_idx}:{part_idx}",
                    role=role,
                    content=content,
                    created_at=ts,
                )
            )
    return out
