import logging
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import Any

from pydantic import ValidationError
from pydantic_ai.run import AgentRunResult
from pydantic_ai.ui.ag_ui import AGUIAdapter
from starlette.requests import Request
from starlette.responses import Response

from flat_chat.chat.agent import agent
from flat_chat.chat.providers import build_chat_model
from flat_chat.chat.session_state import SessionState
from flat_chat.chat.sessions import SessionNotFoundError, SessionStore
from flat_chat.chat.state import ChatDeps
from flat_chat.core.observability import run_id_var, session_id_var
from flat_chat.listings.service import ListingService
from flat_chat.search.places import PlaceService
from flat_chat.search.service import SearchService
from flat_chat.search.transit_routes import TransitRouteService

try:
    from openinference.instrumentation import using_session
except ImportError:  # pragma: no cover — observability is optional
    from contextlib import nullcontext as using_session  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class InvalidAgentRequestError(Exception):
    """The AG-UI request envelope failed validation."""


class LlmProviderUnavailableError(Exception):
    """No LLM provider is configured / could be built for this run."""


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
        transit_route_service: TransitRouteService,
        store: SessionStore,
    ) -> None:
        self.search_service = search_service
        self.listing_service = listing_service
        self.place_service = place_service
        self.transit_route_service = transit_route_service
        self.store = store

    async def dispatch_agent_request(self, request: Request) -> Response:
        # Parse the AG-UI request envelope first so we can resolve the
        # session from its `thread_id` / conversation_id. The adapter
        # subsequently runs the agent, streams events back, and reads
        # `deps.state` to emit JSON-Patch deltas to the frontend.
        try:
            # Subscript the adapter with our deps type — `AgentDepsT` defaults
            # to `None`, so the bare-class classmethod would otherwise type the
            # adapter (and `run_stream(deps=)`) as `None`-deps.
            adapter = await AGUIAdapter[ChatDeps, str].from_request(
                request, agent=agent
            )
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

        # TODO(auth): ownership check. The REST routes (GET /messages, /state)
        # 404 a foreign conversation via `_load_owned`, but this mutation path
        # resolves the session purely from the envelope's thread_id with no
        # `user_id` comparison — so once `get_user_id()` returns a real user
        # (stage 2/3), any caller could continue someone else's thread. Moot
        # today (single dummy user). When auth lands, gate `get()` here on the
        # request user the same way `_load_owned` does. See AUTH.md.
        try:
            session = await self.store.get(session_id)
        except SessionNotFoundError:
            logger.warning("Agent request for unknown session")
            raise

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
            transit_route_service=self.transit_route_service,
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
    """
    async with lock:
        with using_session(session_id):
            async for event in stream:
                yield event
