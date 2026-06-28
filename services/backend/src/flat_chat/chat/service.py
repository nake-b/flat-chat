import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import Any

from ag_ui.core import BaseEvent, EventType, ToolCallResultEvent
from pydantic import ValidationError
from pydantic_ai.messages import ModelMessage, RetryPromptPart, ToolReturnPart
from pydantic_ai.run import AgentRunResult
from pydantic_ai.ui.ag_ui import AGUIAdapter, AGUIEventStream
from starlette.requests import Request
from starlette.responses import Response

from flat_chat.chat.agent import agent
from flat_chat.chat.providers import build_chat_model
from flat_chat.chat.session_state import SessionState
from flat_chat.chat.sessions import SessionNotFoundError, SessionStore
from flat_chat.chat.state import ChatDeps
from flat_chat.chat.title_gen import generate_title, is_first_completed_turn
from flat_chat.core.observability import run_id_var, session_id_var
from flat_chat.listings.service import ListingService
from flat_chat.search.places import PlaceService
from flat_chat.search.service import SearchService

try:
    from openinference.instrumentation import using_session
except ImportError:  # pragma: no cover — observability is optional
    from contextlib import nullcontext as using_session  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class InvalidAgentRequestError(Exception):
    """The AG-UI request envelope failed validation."""


class LlmProviderUnavailableError(Exception):
    """No LLM provider is configured / could be built for this run."""


class _QuietRetryEventStream(AGUIEventStream[ChatDeps, str]):
    """AG-UI event stream that hides tool-retry / validation errors from the UI.

    When the LLM emits invalid tool args (or a tool raises ModelRetry), Pydantic
    AI builds a `RetryPromptPart` and the stock `AGUIEventStream._handle_tool_result`
    unconditionally streams its `model_response()` — the raw "N validation errors…
    Fix the errors and try again." dump — as the tool-call result content. Our
    wildcard status pill echoes that content, so the error leaks into the chat.

    The agent retries the call (a *new* tool_call_id) and usually succeeds, so the
    failure is a transient internal correction the user should never see. We can
    only tell a retry from a real result by *type* (`RetryPromptPart`), and that
    type survives only here on the backend — the frontend receives a flat content
    string with no error flag. So we make the decision here: emit an EMPTY-content
    result for a retry (the frontend renders nothing for an empty result) and let
    real `ToolReturnPart` results flow through untouched.

    Empty content rather than dropping the event entirely keeps CopilotKit's tool
    lifecycle intact (the call still completes → no stuck/pulsing pill).

    Full rationale + why this isn't fixable upstream:
    `agent-compound-docs/decisions/ag-ui-tool-retry-suppression.md`.
    """

    async def _handle_tool_result(
        self, result: ToolReturnPart | RetryPromptPart
    ) -> AsyncIterator[BaseEvent]:
        if isinstance(result, RetryPromptPart):
            yield ToolCallResultEvent(
                message_id=self.new_message_id(),
                type=EventType.TOOL_CALL_RESULT,
                role="tool",
                tool_call_id=result.tool_call_id,
                content="",
            )
            return
        async for event in super()._handle_tool_result(result):
            yield event


class _FlatChatAGUIAdapter(AGUIAdapter[ChatDeps, str]):
    """AG-UI adapter wired to use the retry-suppressing event stream."""

    def build_event_stream(self) -> _QuietRetryEventStream:
        return _QuietRetryEventStream(
            self.run_input, accept=self.accept, ag_ui_version=self.ag_ui_version
        )


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
        listing_service: ListingService,
        place_service: PlaceService,
        store: SessionStore,
    ) -> None:
        self.search_service = search_service
        self.listing_service = listing_service
        self.place_service = place_service
        self.store = store

    async def dispatch_agent_request(self, request: Request, user_id: str) -> Response:
        # Parse the AG-UI request envelope first so we can resolve the
        # session from its `thread_id` / conversation_id. The adapter
        # subsequently runs the agent, streams events back, and reads
        # `deps.state` to emit JSON-Patch deltas to the frontend.
        try:
            # `_FlatChatAGUIAdapter` already binds `AGUIAdapter[ChatDeps, str]`,
            # so deps are typed as ChatDeps (not the `AgentDepsT=None` default)
            # without subscripting — the subclass is concrete, so subscripting it
            # would raise `TypeError: not subscriptable`.
            adapter = await _FlatChatAGUIAdapter.from_request(request, agent=agent)
        except ValidationError as exc:
            raise InvalidAgentRequestError(str(exc)) from exc

        session_id = adapter.conversation_id
        if session_id is None:
            raise InvalidAgentRequestError(
                "AG-UI request envelope has no thread_id / conversation_id"
            )
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

        # Ownership check — mirrors `api/chat.py:_load_owned` for the REST reads.
        # The session is resolved from the envelope's thread_id; gate it on the
        # authenticated `user_id` so a caller who knows (or guesses) a foreign
        # thread_id can't continue or read someone else's conversation through
        # the agent. A mismatch is reported as "not found" (not "forbidden") so
        # existence doesn't leak — same 404-not-403 contract as the REST routes.
        try:
            session = await self.store.get(session_id)
        except SessionNotFoundError:
            logger.warning("Agent request for unknown session")
            raise
        if session.user_id != user_id:
            logger.warning("Agent request for foreign session — 404")
            raise SessionNotFoundError(session_id)

        # Session exists, so lock() will not raise. Resolve the lock here so
        # the inner generator below holds a reference for the stream's
        # lifetime — the `async with` lives inside the generator because
        # StreamingResponse consumes the iterator after the function returns.
        lock = self.store.lock(session_id)

        # Hydrate deps.state. Two sources merge here:
        #   1. The session's persisted SessionState (results, search_params,
        #      etc. — what tools mutated on prior turns and the store saved)
        #   2. The incoming AG-UI envelope's state (frontend-driven changes,
        #      especially `active_id` after a card click + the HTTP-fetched
        #      `active_listing_detail` the frontend wrote back to state)
        # The envelope wins for fields the frontend owns (active_id,
        # active_listing_detail); the session wins for fields the agent
        # owns (results, search_params, total_results).
        # Deep copy: tools currently REASSIGN the tier lists (result_markers /
        # preview_cards) so a shallow copy would be safe today, but a future
        # tool that mutates a list in place (e.g. `.append`) would corrupt the
        # persisted session state mid-run before `on_complete` reassigns it.
        deps_state = session.state.model_copy(deep=True)
        incoming_state = _extract_incoming_state(adapter)
        if incoming_state is not None:
            if incoming_state.active_id is not None:
                deps_state.active_id = incoming_state.active_id
            if incoming_state.active_listing_detail is not None:
                deps_state.active_listing_detail = incoming_state.active_listing_detail

        deps = ChatDeps(
            search_service=self.search_service,
            listing_service=self.listing_service,
            place_service=self.place_service,
            session=session,
            state=deps_state,
        )

        async def on_complete(result: AgentRunResult) -> None:
            # AG-UI sends the full thread on every call; rebuild history
            # from the run result so the GET history endpoint sees the
            # same set the frontend just rendered. SessionState lives on
            # `deps.state` (mutated in place by tools) — assign back to
            # the session before persisting.
            session.message_history = list(result.all_messages())
            session.state = deps.state
            await self.store.save(session)
            logger.info("Agent complete: messages=%d", len(session.message_history))

            # Fire-and-forget title generation on the FIRST completed turn,
            # after persistence has returned. Background-task isolation keeps
            # a cosmetic LLM call off the user-visible critical path; a title
            # failure leaves the row with `title=NULL`, the list endpoint
            # returns null, and the frontend renders "Untitled".
            if session.title is None and is_first_completed_turn(
                session.message_history
            ):
                asyncio.create_task(
                    _generate_and_persist_title(
                        self.store, session.id, session.message_history
                    )
                )

        try:
            model = build_chat_model()
        except RuntimeError as exc:
            raise LlmProviderUnavailableError("No LLM provider configured") from exc

        # History-authoritative recovery. `run_stream` prepends any supplied
        # `message_history` to the envelope's messages. In normal live turns the
        # frontend already carries the full thread, so we pass nothing (passing
        # stored history too would duplicate it). After a reload where the chat
        # transcript wasn't restored, the frontend sends ONLY the new prompt — we
        # detect that (≤1 envelope message) and inject the stored history so the
        # agent keeps full context. The ≤1 test is robust to tool-message count
        # inflation that would break a length comparison. See R3.
        message_history = None
        if session.message_history and len(adapter.messages) <= 1:
            message_history = session.message_history

        stream = adapter.run_stream(
            deps=deps,
            model=model,
            message_history=message_history,
            on_complete=on_complete,
        )
        return adapter.streaming_response(
            _with_session_and_lock(stream, session_id, lock)
        )


def _extract_incoming_state(adapter) -> SessionState | None:
    """Pull frontend-side state edits out of the AG-UI request envelope.

    The adapter exposes the request's `state` field directly. We try to
    parse it as a SessionState; on failure (envelope shape mismatch from
    an old client, partial state, etc.) we return None and the persisted
    session state wins — defensive default keeps a malformed frontend
    push from clobbering known-good server state.
    """
    # The AG-UI envelope surfaces `state` at one of two locations depending
    # on the adapter version (directly on the adapter, or nested under
    # `run_input`), so we probe both. The try/except + isinstance guard below
    # defends against a malformed frontend push — defensive default of None
    # lets the known-good persisted server state win.
    raw = getattr(adapter, "state", None) or getattr(
        getattr(adapter, "run_input", None), "state", None
    )
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


async def _generate_and_persist_title(
    store: SessionStore,
    session_id: str,
    history: list[ModelMessage],
) -> None:
    """Background task: generate a title from the first turn, persist if NULL.

    Catches all exceptions — the title is cosmetic and a failure here must
    never propagate into the user's request loop. The `session_id` ContextVar
    bound in `dispatch_agent_request` does NOT propagate into tasks spawned
    via `create_task` in newer Python versions, but the SQL hook in
    `core/database.py` reads it via `.get("")` which simply omits the SQL
    comment — no error.
    """
    try:
        title = await generate_title(history)
        if title is None:
            return
        updated = await store.set_title_if_unset(session_id, title)
        if updated:
            logger.info("Conversation title set: session=%s title=%r", session_id, title)
    except Exception:
        logger.exception("Background title generation failed for %s", session_id)


async def _with_session_and_lock(
    stream: AsyncIterator[Any],
    session_id: str,
    lock: AbstractAsyncContextManager[object],
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
