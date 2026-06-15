import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from pydantic import ValidationError
from pydantic_ai.run import AgentRunResult
from pydantic_ai.ui.ag_ui import AGUIAdapter
from starlette.requests import Request
from starlette.responses import Response

from flat_chat.chat.agent import agent
from flat_chat.chat.providers import build_chat_model
from flat_chat.chat.sessions import SessionNotFoundError, SessionStore
from flat_chat.chat.state import ChatDeps
from flat_chat.core.observability import run_id_var, session_id_var
from flat_chat.search.service import SearchService

try:
    from openinference.instrumentation import using_session
except ImportError:  # pragma: no cover — observability is optional
    from contextlib import nullcontext as using_session  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _summarise_prompt(run_input: Any) -> str:
    """Last user message as a single short line for the dispatch log.

    Multimodal `content` (a list of input parts) collapses to a `[multimodal]`
    marker so the log stays scannable. Trailing truncation at 120 chars keeps
    one turn = one log line — long pastes don't blow up the stream.
    """
    for msg in reversed(run_input.messages):
        if getattr(msg, "role", None) != "user":
            continue
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            text = content.strip().replace("\n", " ")
            return f'prompt="{text[:120]}{"…" if len(text) > 120 else ""}"'
        if isinstance(content, list):
            return "prompt=[multimodal]"
        break
    return "prompt=<none>"


class ChatService:
    """Orchestrates a single agent run against an AG-UI request.

    Loads the session, assembles ChatDeps (request-scoped services +
    session state + the UiState the AG-UI adapter will set from the
    request body), then hands the request to AGUIAdapter and persists
    the new history + final state when the run completes.

    Knows nothing about FastAPI routing or storage backend internals.
    """

    def __init__(
        self,
        search_service: SearchService,
        store: SessionStore,
    ) -> None:
        self.search_service = search_service
        self.store = store

    async def dispatch_agent_request(self, request: Request) -> Response:
        # Importing here keeps fastapi/starlette as the FastAPI-only deps and
        # leaves room for the service to be wired into non-HTTP entry points.
        from fastapi import HTTPException

        # Parse the AG-UI request envelope first so we can resolve the
        # session from its `thread_id` / conversation_id. The adapter
        # subsequently runs the agent, streams events back, and reads
        # `deps.state` to emit JSON-Patch deltas to the frontend.
        try:
            adapter = await AGUIAdapter.from_request(request, agent=agent)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        session_id = adapter.conversation_id
        # Bind the request context for every log line + every SQL statement
        # that runs within this asyncio task. `session_prefix` (logging filter)
        # and the `before_cursor_execute` hook in `core/database.py` both
        # read these vars. No `.reset()` — FastAPI runs each request in its
        # own asyncio task with its own copied context, so the binding dies
        # with the task. (We tried explicit reset(); Starlette runs the SSE
        # consumer in a different task than the handler that created the
        # Token, so `reset()` raised `Token created in a different Context`.)
        session_id_var.set(session_id or "")
        run_id_var.set(adapter.run_input.run_id or "")
        logger.info("Agent dispatch: %s", _summarise_prompt(adapter.run_input))

        try:
            session = self.store.get(session_id)
        except SessionNotFoundError:
            logger.warning("Agent request for unknown session")
            raise

        # Session exists, so lock() will not raise. Resolve the lock here so
        # the inner generator below holds a reference for the stream's
        # lifetime — the `async with` lives inside the generator because
        # StreamingResponse consumes the iterator after the function returns.
        lock = self.store.lock(session_id)

        deps = ChatDeps(
            search_service=self.search_service,
            session=session,
        )

        async def on_complete(result: AgentRunResult) -> None:
            # AG-UI sends the full thread on every call; rebuild history from
            # the run result so the GET history endpoint sees the same set
            # the frontend just rendered. ui_state mutates in place, so a
            # reference assignment is enough — `state` is the same object
            # we passed in (the adapter uses a setter, not `replace`).
            session.message_history = list(result.all_messages())
            session.ui_state = deps.state
            self.store.save(session)
            logger.info(
                "Agent complete: messages=%d", len(session.message_history)
            )

        try:
            model = build_chat_model()
        except RuntimeError as exc:
            raise HTTPException(
                status_code=503, detail="No LLM provider configured"
            ) from exc

        stream = adapter.run_stream(
            deps=deps,
            model=model,
            on_complete=on_complete,
        )
        return adapter.streaming_response(
            _with_session_and_lock(stream, session_id, lock)
        )


async def _with_session_and_lock(
    stream: AsyncIterator[Any],
    session_id: str,
    lock: asyncio.Lock,
) -> AsyncIterator[Any]:
    """Hold the per-session lock and Phoenix session context for the SSE stream.

    Starlette consumes the inner iterator after the response is returned, so
    both the lock and `using_session(...)` must live inside the generator —
    acquiring them at the call site would release before any events flow.
    Wrapping the generator keeps both active until the stream closes.
    """
    async with lock:
        with using_session(session_id):
            async for event in stream:
                yield event
