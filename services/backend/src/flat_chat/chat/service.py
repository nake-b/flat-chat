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
from flat_chat.search.service import SearchService

try:
    from openinference.instrumentation import using_session
except ImportError:  # pragma: no cover — observability is optional
    from contextlib import nullcontext as using_session  # type: ignore[assignment]

logger = logging.getLogger(__name__)


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
        logger.info("Agent dispatch: session=%s", session_id)

        try:
            session = self.store.get(session_id)
        except SessionNotFoundError:
            logger.warning("Agent request for unknown session %s", session_id)
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
                "Agent complete: session=%s messages=%d",
                session_id,
                len(session.message_history),
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
