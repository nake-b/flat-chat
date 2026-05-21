import logging

from sqlalchemy.orm import Session

from flat_chat.chat.agent import AgentResult, run_agent
from flat_chat.chat.sessions import SessionStore
from flat_chat.chat.state import ChatDeps
from flat_chat.search.service import SearchService

logger = logging.getLogger(__name__)


class ChatService:
    """Orchestrates a single user message against the agent.

    Loads the session, assembles ChatDeps (request-scoped services +
    session state), runs the agent, then extends and saves the session's
    message history. Knows nothing about HTTP or storage backend.
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

    async def send_message(self, session_id: str, content: str) -> AgentResult:
        # Serialize concurrent requests on the same conversation so message
        # history isn't corrupted by interleaved appends. Cheap insurance
        # against double-clicks / retries; deletable when sessions move to DB.
        async with self.store.lock(session_id):
            session = self.store.get(session_id)
            deps = ChatDeps(
                db=self.db,
                search_service=self.search_service,
                session=session,
            )
            try:
                result = await run_agent(content, deps)
            except Exception:
                logger.exception("LLM call failed")
                raise
            session.message_history.extend(result.new_messages)
            self.store.save(session)
            return result
