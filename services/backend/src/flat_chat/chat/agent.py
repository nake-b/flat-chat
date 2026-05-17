from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pandas as pd
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from flat_chat.search.schemas import SearchFilters
    from flat_chat.search.service import SearchService


@dataclass
class ResultSet:
    df: pd.DataFrame
    filters: SearchFilters
    total: int


@dataclass
class ChatDeps:
    db: Session
    search_service: SearchService
    result_set: ResultSet | None = field(default=None)


agent = Agent(
    deps_type=ChatDeps,
    instructions=(
        "You are a helpful Berlin apartment search assistant. "
        "You help users find apartments in Berlin by asking about their preferences "
        "(budget, neighborhood, size, move-in date, furnished/unfurnished, etc.) "
        "and providing relevant advice about Berlin's rental market. "
        "Be concise, friendly, and practical. "
        "If users ask about things unrelated to apartment searching in Berlin, "
        "gently steer them back to the topic."
    ),
    tool_retries=3,
)


@agent.instructions
def add_result_context(ctx: RunContext[ChatDeps]) -> str:
    if ctx.deps.result_set:
        rs = ctx.deps.result_set
        return (
            f"Current result set: {rs.total} listings. "
            f"Filters: {rs.filters.model_dump(exclude_none=True)}"
        )
    return "No active search results."


def _get_model() -> str:
    from flat_chat.core.config import settings

    return f"openrouter:{settings.llm_model}"


@dataclass
class AgentResult:
    output: str
    new_messages: list[ModelMessage]


async def run_agent(
    user_message: str,
    message_history: list[ModelMessage],
    deps: ChatDeps,
) -> AgentResult:
    import flat_chat.chat.tools  # noqa: F401 — registers tools on agent

    result = await agent.run(
        user_message,
        model=_get_model(),
        deps=deps,
        message_history=message_history,
    )
    return AgentResult(
        output=result.output,
        new_messages=result.new_messages(),
    )
