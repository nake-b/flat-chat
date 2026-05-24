from fastapi import APIRouter, Depends, HTTPException
from starlette.requests import Request
from starlette.responses import Response

from flat_chat.chat.service import ChatService
from flat_chat.chat.sessions import SessionNotFoundError
from flat_chat.core.dependencies import get_chat_service

router = APIRouter()


# Reply shape for CopilotKit's runtime discovery probe. Sent in response to
# both `GET /api/agent/info` and `POST /api/agent` with `{method:"info"}`.
# Returning a healthy "no managed agents" body silences the dev-console
# auto-open and the "runtime info request failed" red banner.
_RUNTIME_INFO: dict[str, object] = {
    "agents": [],
    "actions": [],
    "sdkVersion": "self-managed",
}


@router.get("/info")
def runtime_info_get() -> dict[str, object]:
    return _RUNTIME_INFO


@router.get("/threads")
def runtime_threads() -> dict[str, list[object]]:
    """CopilotKit also queries threads at boot; return an empty list."""
    return {"threads": []}


@router.post("")
async def run_agent(
    request: Request,
    chat: ChatService = Depends(get_chat_service),
) -> Response:
    """AG-UI streaming endpoint.

    The frontend (CopilotKit + `useCoAgent`) POSTs an AG-UI envelope here
    that carries `thread_id` (= session id, created via POST /api/conversations),
    the running message history, and the current UiState. The adapter streams
    SSE events back: text deltas, tool-call lifecycle, and JSON-Patch state
    deltas that mutate the frontend's mirrored `UiState` slice.

    CopilotKit also POSTs `{method: "info"}` to this same URL at boot for
    runtime discovery — short-circuit that before handing the request to the
    AG-UI adapter (which would otherwise 422 on the missing AG-UI fields).
    """
    # NOTE on CopilotKit's runtime-discovery probe: at boot the client POSTs
    # `{"method":"info"}` to this URL to ask the runtime what agents exist.
    # We deliberately *don't* short-circuit that here — returning a synthetic
    # `agents` list makes CopilotKit route messages to it via the runtime
    # client, bypassing the `agents__unsafe_dev_only` HttpAgent we wired on
    # the React side. Letting the probe 422 is harmless: CopilotKit logs a
    # warning, then falls back to the self-managed agent. The visible
    # dev-console nag is suppressed by the `<cpk-web-inspector>` hider in
    # `main.tsx`.
    try:
        response = await chat.dispatch_agent_request(request)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc

    # Belt-and-braces against intermediate proxies that re-enable buffering:
    # signal that no buffering should happen on the streaming response.
    response.headers["X-Accel-Buffering"] = "no"
    return response
