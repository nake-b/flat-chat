import logging

from flat_chat.llm.gateway import get_completion

logger = logging.getLogger(__name__)


class ChatService:
    async def send_message(
        self, content: str, history: list[dict]
    ) -> str:
        messages = [
            {"role": m["role"], "content": m["content"]} for m in history
        ]
        messages.append({"role": "user", "content": content})

        try:
            return await get_completion(messages)
        except Exception:
            logger.exception("LLM call failed")
            return (
                "I'm sorry, I'm having trouble responding right now. "
                "Please try again in a moment."
            )
