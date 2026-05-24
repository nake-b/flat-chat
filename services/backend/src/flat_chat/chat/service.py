import logging

from openinference.instrumentation import using_session
from pydantic_ai.run import AgentRunResult
from pydantic_ai.ui.ag_ui import AGUIAdapter
from sqlalchemy.orm import Session
from starlette.requests import Request
from starlette.responses import Response

from flat_chat.chat.agent import agent
from flat_chat.chat.providers import build_chat_model
from flat_chat.chat.sessions import SessionNotFoundError, SessionStore
from flat_chat.chat.state import ChatDeps
from flat_chat.search.service import SearchService

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
        db: Session,
        search_service: SearchService,
        store: SessionStore,
    ) -> None:
        self.db = db
        self.search_service = search_service
        self.store = store

    async def dispatch_agent_request(self, request: Request) -> Response:
        # Parse the AG-UI request envelope first so we can resolve the
        # session from its `thread_id` / conversation_id. The adapter
        # subsequently runs the agent, streams events back, and reads
        # `deps.state` to emit JSON-Patch deltas to the frontend.
        adapter = await AGUIAdapter.from_request(request, agent=agent)
        session_id = adapter.conversation_id

        try:
            session = self.store.get(session_id)
        except SessionNotFoundError:
            logger.warning("Agent request for unknown session %s", session_id)
            raise

        deps = ChatDeps(
            db=self.db,
            search_service=self.search_service,
            session=session,
            state=session.ui_state,
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

        with using_session(session_id):
            return adapter.streaming_response(
                adapter.run_stream(
                    deps=deps,
                    model=build_chat_model(),
                    on_complete=on_complete,
                )
            )
