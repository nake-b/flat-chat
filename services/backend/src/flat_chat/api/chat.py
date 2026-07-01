from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic_ai.ui.ag_ui import DEFAULT_AG_UI_VERSION, AGUIAdapter

from flat_chat.chat.schemas import (
    ConversationResponse,
    ConversationSummary,
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

    return _collapse_search_finishes(kept)


def _collapse_search_finishes(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return `messages` with every `search_apartments` result blanked except
    the LAST one in its turn — so an auto-broadening turn (0 → 0 → 48) restores
    a single "Found 48 apartments", not a stack. A turn is the span between user
    messages; results are matched to their originating tool call via `toolCallId`.

    Pure: the input list is not mutated. Only the blanked messages are rebuilt
    (shallow copies with `content` cleared); every other message is passed
    through by reference.
    """
    # `dump_messages` emits `toolCalls: null` for non-tool messages, so `or []`
    # (not `.get("toolCalls", [])`) is load-bearing: the default only fills a
    # MISSING key, whereas an explicit `null` would slip through and break `for`.
    name_by_call_id: dict[str, str] = {}
    for msg in messages:
        for call in msg.get("toolCalls") or []:
            call_id = call.get("id")
            name = (call.get("function") or {}).get("name")
            if call_id and name:
                name_by_call_id[call_id] = name

    # Collect the indices to blank: within each turn, every search result but
    # the last. `turn` accumulates the search-result indices since the last user
    # message; closing a turn marks all-but-the-final for blanking.
    blank_idxs: set[int] = set()
    turn: list[int] = []

    def close_turn() -> None:
        blank_idxs.update(turn[:-1])
        turn.clear()

    for idx, msg in enumerate(messages):
        if msg.get("role") == "user":
            close_turn()
        elif msg.get("role") == "tool":
            call_id = msg.get("toolCallId")
            if call_id and name_by_call_id.get(call_id) == SEARCH_TOOL_NAME:
                turn.append(idx)
    close_turn()

    return [
        {**msg, "content": ""} if idx in blank_idxs else msg
        for idx, msg in enumerate(messages)
    ]
