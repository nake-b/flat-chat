from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic_ai.messages import TextPart, UserPromptPart

from flat_chat.chat.schemas import (
    ConversationResponse,
    ConversationSummary,
    MessageResponse,
    SessionStateResponse,
)
from flat_chat.chat.sessions import SessionNotFoundError, SessionStore
from flat_chat.chat.state import ChatSession
from flat_chat.core.dependencies import get_session_store, get_user_id

router = APIRouter()


@router.post("", response_model=ConversationResponse)
async def create_conversation(
    user_id: str = Depends(get_user_id),
    store: SessionStore = Depends(get_session_store),
):
    """Allocate a new conversation. The returned id is used as the AG-UI `thread_id`."""
    session = await store.create(user_id)
    return ConversationResponse(id=session.id, created_at=session.created_at)


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    user_id: str = Depends(get_user_id),
    store: SessionStore = Depends(get_session_store),
) -> list[ConversationSummary]:
    """Powers the conversation-list sidebar.

    Returns the calling user's conversations that have at least one message
    (empty threads from a "+ New chat" click that never sent a prompt are
    filtered out). Newest-first by `updated_at`. Empty list when the user has
    no rows — never 404.
    """
    return await store.list_conversation_summaries(user_id)


async def _load_owned(
    conversation_id: str, user_id: str, store: SessionStore
) -> ChatSession:
    """Load a conversation, 404ing if it's missing OR owned by someone else.

    Returns 404 (not 403) for a foreign conversation so existence doesn't leak.
    """
    try:
        session = await store.get(conversation_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc
    if session.user_id != user_id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return session


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str,
    user_id: str = Depends(get_user_id),
    store: SessionStore = Depends(get_session_store),
) -> Response:
    """Hard-delete a conversation owned by the caller.

    Cascades to `app.messages` and `app.session_state` via FK
    `ON DELETE CASCADE`. Returns 204 on success; 404 (NOT 403) for missing
    OR foreign rows so existence doesn't leak across users.
    """
    deleted = await store.delete_if_owned(conversation_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return Response(status_code=204)


@router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
async def get_messages(
    conversation_id: str,
    user_id: str = Depends(get_user_id),
    store: SessionStore = Depends(get_session_store),
):
    """History reload — used by the frontend after a page refresh.

    Sending new messages goes through the AG-UI streaming route at /api/agent;
    this endpoint is read-only and only surfaces user + assistant turns.
    """
    session = await _load_owned(conversation_id, user_id, store)
    return _serialize_history(session)


@router.get("/{conversation_id}/state", response_model=SessionStateResponse)
async def get_state(
    conversation_id: str,
    user_id: str = Depends(get_user_id),
    store: SessionStore = Depends(get_session_store),
) -> dict[str, Any]:
    """Latest SessionState snapshot — the cross-reload recovery primitive.

    Returns the same shape the AG-UI `STATE_SNAPSHOT` event emits (markers in
    columnar form, preview cards, active listing), so the frontend mirror can
    apply it directly via `useCoAgent().setState`. A conversation with no turns
    yet returns the default/empty SessionState.

    The body is `SessionState.model_dump(mode="json")` (already columnar via the
    field serializer); `response_model=SessionStateResponse` types that exact
    wire shape so the OpenAPI schema is accurate rather than `object`.
    """
    session = await _load_owned(conversation_id, user_id, store)
    return session.state.model_dump(mode="json")


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
