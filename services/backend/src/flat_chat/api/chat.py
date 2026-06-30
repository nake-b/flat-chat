from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic_ai.ui.ag_ui import DEFAULT_AG_UI_VERSION, AGUIAdapter

from flat_chat.chat.schemas import (
    ConversationResponse,
    SessionStateResponse,
)
from flat_chat.chat.sessions import SessionNotFoundError, SessionStore
from flat_chat.chat.state import ChatSession
from flat_chat.chat.tools import SEARCH_TOOL_NAME
from flat_chat.core.dependencies import get_session_store, get_user_id

router = APIRouter()

# AG-UI message roles that are ephemeral UI affordances, not transcript: the
# "Thinking…" indicator (reasoning) and transient activity messages. Dropped on
# reload so they don't become persisted bubbles.
_EPHEMERAL_ROLES = frozenset({"reasoning", "activity"})


@router.post("", response_model=ConversationResponse)
async def create_conversation(
    user_id: str = Depends(get_user_id),
    store: SessionStore = Depends(get_session_store),
):
    """Allocate a new conversation. The returned id is used as the AG-UI `thread_id`."""
    session = await store.create(user_id)
    return ConversationResponse(id=session.id, created_at=session.created_at)


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


@router.get("/{conversation_id}/messages", response_model=list[dict[str, Any]])
async def get_messages(
    conversation_id: str,
    user_id: str = Depends(get_user_id),
    store: SessionStore = Depends(get_session_store),
) -> list[dict[str, Any]]:
    """History reload — used by the frontend after a page refresh.

    Returns the full AG-UI message stream (text + tool calls + tool results) so
    the frontend restores the same transcript it rendered live — tool "finishes"
    (e.g. "Found 12 apartments") are messages and persist. Sending new messages
    goes through the AG-UI streaming route at /api/agent; this endpoint is
    read-only.
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


def _serialize_history(session: ChatSession) -> list[dict[str, Any]]:
    """Project the agent's ModelMessage history into the AG-UI message stream
    the frontend restores on reload — text PLUS tool calls/results, so tool
    "finishes" persist exactly as they render live (issue #22).

    `AGUIAdapter.dump_messages` is the canonical ModelMessage → AG-UI converter
    (the same message shapes the live SSE stream emits). `by_alias=True` is
    load-bearing: the frontend (CopilotKit) keys tool rendering off camelCase
    `toolCalls` / `toolCallId`.

    We then apply the same "let-through" policy the live UI uses, so the restored
    transcript matches what was on screen:
      - Thinking is ephemeral → drop `reasoning` / `activity` messages.
      - Tool retries show nothing → blank tool messages carrying an `error`
        (mirrors `_QuietRetryEventStream` on the live path).
      - One search finish per turn → keep only the LAST `search_apartments`
        result in each turn (turns split at user messages); a turn that broadens
        several times must not stack identical lines.
    Blanked tool messages keep their `toolCallId` but render nothing through the
    frontend's wildcard tool-pill path.
    """
    dumped = AGUIAdapter.dump_messages(
        list(session.message_history), ag_ui_version=DEFAULT_AG_UI_VERSION
    )

    kept: list[dict[str, Any]] = []
    for message in dumped:
        msg = message.model_dump(by_alias=True)
        if msg.get("role") in _EPHEMERAL_ROLES:
            continue
        if msg.get("role") == "tool" and msg.get("error"):
            msg["content"] = ""
        kept.append(msg)

    _collapse_search_finishes(kept)
    return kept


def _collapse_search_finishes(messages: list[dict[str, Any]]) -> None:
    """In place: keep only the LAST `search_apartments` tool result per turn.

    A turn is the span between user messages. Within it, tool results whose
    originating tool call (joined by `toolCallId`) is `search_apartments` get
    their content blanked except the final one — so an auto-broadening turn
    (0 → 0 → 48) shows a single "Found 48 apartments", not a stack.
    """
    name_by_call_id: dict[str, str] = {}
    for msg in messages:
        for call in msg.get("toolCalls") or []:
            call_id = call.get("id")
            name = (call.get("function") or {}).get("name")
            if call_id and name:
                name_by_call_id[call_id] = name

    turn_search_idxs: list[int] = []

    def flush_turn() -> None:
        for idx in turn_search_idxs[:-1]:
            messages[idx]["content"] = ""
        turn_search_idxs.clear()

    for idx, msg in enumerate(messages):
        if msg.get("role") == "user":
            flush_turn()
        elif msg.get("role") == "tool":
            call_id = msg.get("toolCallId")
            if call_id and name_by_call_id.get(call_id) == SEARCH_TOOL_NAME:
                turn_search_idxs.append(idx)
    flush_turn()
