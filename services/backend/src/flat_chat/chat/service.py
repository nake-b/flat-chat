"""ChatService — the HTTP/SSE plumbing around your agent.

You almost certainly do NOT need to edit this file for the hackathon. It:

  1. Parses the incoming AG-UI request envelope (`thread_id`, `messages`,
     `state`).
  2. Resolves the conversation from the session store and hydrates
     `ChatDeps.state` (merging persisted server state with the frontend's
     latest `active_id` / `active_listing_detail`).
  3. Brackets your `AgentBackend.run(...)` stream with `RUN_STARTED` /
     `RUN_FINISHED`, SSE-encodes every event you yield, and streams it back.
  4. Persists the thread + final state when your run completes.

Your code lives in an `AgentBackend` (see `chat/backend.py` and
`chat/example_backend.py`). This class knows nothing about how that
backend reaches its answer — only that it yields `ag_ui` events.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from ag_ui.core import (
    RunAgentInput,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageContentEvent,
)
from ag_ui.encoder import EventEncoder
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from flat_chat.chat.backend import AgentBackend
from flat_chat.chat.session_state import SessionState
from flat_chat.chat.sessions import SessionNotFoundError, SessionStore
from flat_chat.chat.state import ChatDeps, ChatMessage
from flat_chat.core.observability import run_id_var, session_id_var
from flat_chat.listings.service import ListingService
from flat_chat.search.service import SearchService

try:
    from openinference.instrumentation import using_session
except ImportError:  # pragma: no cover — observability is optional
    from contextlib import nullcontext as using_session  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _summarise_prompt(run_input: RunAgentInput) -> str:
    """Last user message as a single short line for the dispatch log."""
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
    """Orchestrates one agent-backend run against an AG-UI request."""

    def __init__(
        self,
        search_service: SearchService,
        listing_service: ListingService,
        store: SessionStore,
        backend: AgentBackend,
    ) -> None:
        self.search_service = search_service
        self.listing_service = listing_service
        self.store = store
        self.backend = backend

    async def dispatch_agent_request(self, request: Request) -> Response:
        from fastapi import HTTPException

        try:
            run_input = RunAgentInput.model_validate_json(await request.body())
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        session_id = run_input.thread_id
        # Bind the request context for every log line + every SQL statement
        # fired within this asyncio task. No `.reset()` — FastAPI runs each
        # request in its own task with its own copied context, so the binding
        # dies with the task.
        session_id_var.set(session_id or "")
        run_id_var.set(run_input.run_id or "")
        logger.info("Agent dispatch: %s", _summarise_prompt(run_input))

        try:
            session = self.store.get(session_id)
        except SessionNotFoundError:
            logger.warning("Agent request for unknown session")
            raise

        # Resolve the lock here; the SSE generator below holds it for the
        # stream's lifetime (StreamingResponse consumes the iterator after
        # this function returns).
        lock = self.store.lock(session_id)

        # Hydrate deps.state: persisted server state is the base; the frontend
        # owns `active_id` + `active_listing_detail` (set on card click), so
        # the incoming envelope wins for those two fields.
        deps_state = session.state.model_copy()
        incoming = _extract_incoming_state(run_input.state)
        if incoming is not None:
            if incoming.active_id is not None:
                deps_state.active_id = incoming.active_id
            if incoming.active_listing_detail is not None:
                deps_state.active_listing_detail = incoming.active_listing_detail

        deps = ChatDeps(
            search_service=self.search_service,
            listing_service=self.listing_service,
            session=session,
            state=deps_state,
        )

        encoder = EventEncoder(accept=request.headers.get("accept"))
        stream = self._event_stream(run_input, deps, session, lock, encoder)
        return StreamingResponse(stream, media_type=encoder.get_content_type())

    async def _event_stream(
        self,
        run_input: RunAgentInput,
        deps: ChatDeps,
        session,
        lock: asyncio.Lock,
        encoder: EventEncoder,
    ) -> AsyncIterator[str]:
        """RUN_STARTED → (your backend's events) → RUN_FINISHED, SSE-encoded.

        The per-session lock and `using_session(...)` context live inside the
        generator: Starlette consumes the iterator after the response is
        returned, so acquiring them at the call site would release too early.
        """
        async with lock:
            with using_session(session.id):
                yield encoder.encode(
                    RunStartedEvent(
                        thread_id=run_input.thread_id, run_id=run_input.run_id
                    )
                )
                reply_parts: list[str] = []
                try:
                    async for event in self.backend.run(
                        run_input=run_input, deps=deps
                    ):
                        if isinstance(event, TextMessageContentEvent):
                            reply_parts.append(event.delta)
                        yield encoder.encode(event)
                except Exception as exc:  # noqa: BLE001 — surface to the client
                    logger.exception("Agent backend failed")
                    yield encoder.encode(
                        RunErrorEvent(message=str(exc) or exc.__class__.__name__)
                    )
                    return
                yield encoder.encode(
                    RunFinishedEvent(
                        thread_id=run_input.thread_id, run_id=run_input.run_id
                    )
                )
                self._persist(session, run_input, deps, "".join(reply_parts))

    def _persist(
        self,
        session,
        run_input: RunAgentInput,
        deps: ChatDeps,
        assistant_reply: str,
    ) -> None:
        """Rebuild the visible thread + save the mutated state.

        AG-UI sends the full thread on every call, so history is the incoming
        user/assistant messages plus the reply this run produced. SessionState
        was mutated in place on `deps.state`.
        """
        history = _history_from_messages(run_input)
        if assistant_reply:
            history.append(ChatMessage(role="assistant", content=assistant_reply))
        session.message_history = history
        session.state = deps.state
        self.store.save(session)
        logger.info("Agent complete: messages=%d", len(session.message_history))


def _history_from_messages(run_input: RunAgentInput) -> list[ChatMessage]:
    """Project AG-UI thread messages into the persisted user-visible history."""
    out: list[ChatMessage] = []
    for msg in run_input.messages:
        role = getattr(msg, "role", None)
        content = getattr(msg, "content", None)
        if role in ("user", "assistant") and isinstance(content, str) and content:
            out.append(ChatMessage(role=role, content=content))
    return out


def _extract_incoming_state(raw: Any) -> SessionState | None:
    """Parse frontend-side state edits out of the AG-UI envelope.

    On any shape mismatch we return None and the persisted server state wins —
    a malformed frontend push must not clobber known-good state.
    """
    if raw is None:
        return None
    try:
        if isinstance(raw, dict):
            return SessionState.model_validate(raw)
        if isinstance(raw, SessionState):
            return raw
    except Exception as exc:  # pragma: no cover — defensive logging
        logger.warning("Could not parse incoming state from envelope: %s", exc)
    return None
