import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import Any

from ag_ui.core import (
    BaseEvent,
    EventType,
    RunErrorEvent,
    RunFinishedEvent,
    TextMessageStartEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from pydantic import ValidationError
from pydantic_ai.messages import ModelMessage, RetryPromptPart, ToolReturnPart
from pydantic_ai.run import AgentRunResult
from pydantic_ai.ui.ag_ui import AGUIAdapter, AGUIEventStream
from starlette.requests import Request
from starlette.responses import Response

from flat_chat.chat.agent import agent
from flat_chat.chat.providers import build_chat_model, build_title_model
from flat_chat.chat.session_state import SessionState
from flat_chat.chat.sessions import SessionNotFoundError, SessionStore
from flat_chat.chat.state import ChatDeps
from flat_chat.chat.title_gen import TitleGenerationService, is_first_completed_turn
from flat_chat.chat.tools import SEARCH_TOOL_NAME
from flat_chat.core.observability import run_id_var, session_id_var
from flat_chat.listings.service import ListingService
from flat_chat.routing.service import RoutingService
from flat_chat.search.distance import DistanceService
from flat_chat.search.places import PlaceService
from flat_chat.search.service import SearchService
from flat_chat.search.transit_overlays import TransitOverlayService

try:
    from openinference.instrumentation import using_session
except ImportError:  # pragma: no cover — observability is optional
    from contextlib import nullcontext as using_session  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Strong references to fire-and-forget background tasks (title generation).
# `asyncio.create_task` returns a task the event loop only weakly references, so
# without holding it here the task can be garbage-collected mid-execution. Each
# task removes itself via `add_done_callback(_background_tasks.discard)`.
_background_tasks: set[asyncio.Task[None]] = set()


class InvalidAgentRequestError(Exception):
    """The AG-UI request envelope failed validation."""


class LlmProviderUnavailableError(Exception):
    """No LLM provider is configured / could be built for this run."""


class _FlatChatEventStream(AGUIEventStream[ChatDeps, str]):
    """AG-UI event stream that shapes which tool "finishes" reach the UI.

    Two transformations, both expressing the same rule the reload path applies in
    `api/chat.py:_serialize_history` — so what's on screen live and what comes back
    after a refresh match:

    1. **Retry suppression** (`_handle_tool_result`): a `RetryPromptPart` (invalid
       tool args / `ModelRetry`) would otherwise stream its raw "N validation
       errors…" dump as the tool result, which the wildcard status pill echoes.
       The agent retries with a new tool_call_id and usually succeeds, so the
       failure is an internal correction the user should never see. We emit an
       EMPTY-content result (renders nothing, lifecycle still completes — no stuck
       pill). The decision is by *type*, which only survives here on the backend.
       See `agent-compound-docs/decisions/ag-ui-tool-retry-suppression.md`.

    2. **Search-finish collapse** (`transform_stream`): within one turn the agent
       may run several `search_apartments` calls (search → 0 → broaden → search
       again). Each result's pill, once shown, can't be cleared by CopilotKit, so
       to avoid a stack we HOLD a search's result instead of emitting it
       immediately. When the NEXT search starts, the held (now superseded) result
       is completed EMPTY — its first-and-only result event, so its pill resolves
       to nothing (no lingering "Searching…", no two "Searching…" at once). The
       turn's LAST held search is flushed WITH content at the answer text / run
       end, so exactly one finish ("Found N" / "No apartments found") survives per
       turn. The reload path (`api/chat.py`) collapses identically.
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

    async def transform_stream(
        self, stream, on_complete=None
    ) -> AsyncIterator[BaseEvent]:  # type: ignore[override]
        search_call_ids: set[str] = set()
        pending: ToolCallResultEvent | None = (
            None  # held search result, not yet emitted
        )

        def _blank(ev: ToolCallResultEvent) -> ToolCallResultEvent:
            return ToolCallResultEvent(
                message_id=ev.message_id,
                type=EventType.TOOL_CALL_RESULT,
                role="tool",
                tool_call_id=ev.tool_call_id,
                content="",
            )

        async for event in super().transform_stream(stream, on_complete):
            if isinstance(event, ToolCallStartEvent):
                if event.tool_call_name == SEARCH_TOOL_NAME:
                    search_call_ids.add(event.tool_call_id)
                    # New search supersedes the held one → resolve its pill to
                    # empty BEFORE this search's "Searching…" shows, so they never
                    # stack.
                    if pending is not None:
                        yield _blank(pending)
                        pending = None
                yield event
                continue

            if (
                isinstance(event, ToolCallResultEvent)
                and event.tool_call_id in search_call_ids
            ):
                pending = event  # hold (don't emit yet)
                continue

            # Answer text begins / run ends → the held search was the turn's last;
            # emit its finish with content (anchored to its call).
            if pending is not None and isinstance(
                event, (TextMessageStartEvent, RunFinishedEvent)
            ):
                yield pending
                pending = None

            yield event

        if pending is not None:  # safety net
            yield pending


class _FlatChatAGUIAdapter(AGUIAdapter[ChatDeps, str]):
    """AG-UI adapter wired to use the finish-shaping event stream."""

    def build_event_stream(self) -> _FlatChatEventStream:
        return _FlatChatEventStream(
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
        transit_overlay_service: TransitOverlayService,
        routing_service: RoutingService,
        distance_service: DistanceService,
        store: SessionStore,
    ) -> None:
        self.search_service = search_service
        self.listing_service = listing_service
        self.place_service = place_service
        self.transit_overlay_service = transit_overlay_service
        self.routing_service = routing_service
        self.distance_service = distance_service
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

        # Hydrate deps.state by merging the persisted server state (agent-owned
        # fields) with the incoming AG-UI envelope (frontend-owned fields). The
        # ownership rule lives in `merge_incoming_state` — one edit-site when a
        # frontend-owned field is added.
        incoming_state = _extract_incoming_state(adapter)
        deps_state = merge_incoming_state(session.state, incoming_state)

        deps = ChatDeps(
            search_service=self.search_service,
            listing_service=self.listing_service,
            place_service=self.place_service,
            transit_overlay_service=self.transit_overlay_service,
            routing_service=self.routing_service,
            distance_service=self.distance_service,
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
                task = asyncio.create_task(
                    _generate_and_persist_title(
                        self.store, session.id, session.message_history
                    )
                )
                # Hold a strong reference until the task finishes — the loop
                # only weakly references it, so without this it could be GC'd
                # mid-run and the conversation would stay "Untitled".
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)

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
    never propagate into the user's request loop. `asyncio.create_task` runs
    the coroutine in a COPY of the current context, so the `session_id`
    ContextVar bound in `dispatch_agent_request` DOES propagate (a snapshot
    taken at task-creation time) and the SQL hook in `core/database.py` tags
    this task's statements with the same session. Either way it reads via
    `.get("")`, so a missing value would just omit the SQL comment — no error.
    """
    try:
        try:
            model = build_title_model()
        except RuntimeError as exc:
            logger.warning("Title model unavailable: %s", exc)
            return
        title = await TitleGenerationService(model).generate(history)
        if title is None:
            return
        updated = await store.set_title_if_unset(session_id, title)
        if updated:
            logger.info(
                "Conversation title set: session=%s title=%r", session_id, title
            )
    except Exception:
        logger.exception("Background title generation failed for %s", session_id)


# Fields the FRONTEND owns — the only ones an incoming envelope may change.
# Everything else (results, search_params, total_results, overlay *content*)
# is agent-owned: the persisted server state always wins, so a malformed or
# stale frontend push can never clobber it. See agent-vs-http-data-flow.md and
# session-state-design.md.
_FRONTEND_OWNED_SCALAR_FIELDS = ("active_id", "active_listing_detail")


def merge_incoming_state(
    persisted: SessionState, incoming: SessionState | None
) -> SessionState:
    """Build the per-run SessionState from persisted (server) + incoming (UI).

    Deep-copies `persisted` (tools currently REASSIGN the tier lists, but a
    future in-place `.append` would otherwise corrupt the stored session
    mid-run before `on_complete` reassigns it), then layers the frontend-owned
    fields on top:

    - `active_id` / `active_listing_detail` — the card the user clicked + the
      tier-3 detail the frontend HTTP-fetched and wrote back. Applied when
      present so the agent's next turn already has the user's focus.
    - `map_overlays` — the frontend may only **remove** overlays (the user
      dismissing one), never add them. We keep persisted overlays whose `id` is
      still present in the incoming set; additions in the envelope are ignored
      (overlay content is agent-owned). This makes dismissal sticky and
      agent-visible without letting the UI inject geometry.

      Subtlety: absence-from-incoming is read as *dismissal*, which is correct
      only because CopilotKit applies the agent's `StateSnapshotEvent` (the
      freshly-drawn overlay) during the SSE stream, and the composer is locked
      until the stream ends — so the next envelope always reflects the latest
      drawn set. A future "send while streaming" path would break that
      invariant (a just-drawn overlay could be absent and get dropped); it would
      need an explicit dismissed-id list rather than set-difference.

    `incoming is None` (parse failure / pre-overlay client) → persisted wins
    untouched.
    """
    merged = persisted.model_copy(deep=True)
    if incoming is None:
        return merged

    if incoming.active_id is not None:
        merged.active_id = incoming.active_id
    if incoming.active_listing_detail is not None:
        merged.active_listing_detail = incoming.active_listing_detail

    # Dismissal: intersect persisted overlays with the ids the frontend still
    # shows. Only shrinks the set — never adds.
    visible_ids = {o.id for o in incoming.map_overlays}
    merged.map_overlays = [o for o in merged.map_overlays if o.id in visible_ids]

    # Lens dismissal (the × on the lens legend): the frontend may only CLEAR the
    # active lens, never set one. If the persisted state had a lens (travel or
    # distance) and the incoming envelope has dropped it, honour the clear —
    # recolour-only, the result set is kept (same shrink-only authority as
    # overlays). Also drop the lens's own anchor overlay (origin="lens") so it
    # doesn't linger; `marker_lens` is computed from `active_lens`, so it needs
    # no reset. Setting a lens stays agent-only (`apply_*_lens`).
    if persisted.active_lens is not None and incoming.active_lens is None:
        merged.active_lens = None
        merged.map_overlays = [o for o in merged.map_overlays if o.origin != "lens"]

    return merged


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

    Also the last line of defence against a mid-run failure. If the agent run
    raises (e.g. the LLM provider errors after its own retries are exhausted),
    the exception would otherwise propagate into Starlette's SSE writer and the
    stream would just die — no run-finished, no error, a frozen "thinking" pill.
    We catch it, log it in full (this is where the provider exception class is
    visible), and emit a terminal `RUN_ERROR` so the frontend resolves the pill
    and can tell the user to retry, instead of hanging forever.
    """
    async with lock:
        with using_session(session_id):
            try:
                async for event in stream:
                    yield event
            except Exception:
                logger.exception("Agent run failed mid-stream — emitting RUN_ERROR")
                yield RunErrorEvent(
                    type=EventType.RUN_ERROR,
                    message=(
                        "Sorry — I hit a problem reaching the model. "
                        "Please try that again."
                    ),
                )
