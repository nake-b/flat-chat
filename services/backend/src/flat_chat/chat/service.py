import logging

from pydantic_ai.messages import ModelMessage
from sqlalchemy.orm import Session

from flat_chat.chat.agent import AgentResult, ChatDeps, run_agent
from flat_chat.search.service import SearchService

logger = logging.getLogger(__name__)


class ChatService:
    def __init__(self, db: Session):
        self.db = db

    async def send_message(
        self, content: str, message_history: list[ModelMessage]
    ) -> AgentResult:
        search_service = SearchService(db=self.db)
        deps = ChatDeps(db=self.db, search_service=search_service)
        try:
            return await run_agent(content, message_history, deps)
        except Exception:
            logger.exception("LLM call failed")
            raise
