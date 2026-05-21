from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage

from flat_chat.chat.providers import build_chat_model
from flat_chat.chat.state import ChatDeps
from flat_chat.chat.tools import toolset

INSTRUCTIONS = (
    "You are a helpful Berlin apartment search assistant. "
    "You help users find apartments in Berlin by asking about their preferences "
    "(budget, neighborhood, size, move-in date, furnished/unfurnished, etc.) "
    "and providing relevant advice about Berlin's rental market. "
    "Be concise, friendly, and practical. "
    "If users ask about things unrelated to apartment searching in Berlin, "
    "gently steer them back to the topic. "
    "When you receive a result set from search_apartments, the tool output ends "
    "with an explicit menu of follow-up calls — use get_result_page to show more "
    "and get_result_details to inspect specific listings the user is interested in. "
    "To refine the active search, repeat ALL filters you want to keep — omitted "
    "args are dropped. Never volunteer a filter the user did not explicitly ask for."
)


@dataclass
class AgentResult:
    output: str
    new_messages: list[ModelMessage]


agent: Agent[ChatDeps, str] = Agent(
    deps_type=ChatDeps,
    toolsets=[toolset],
    instructions=INSTRUCTIONS,
    tool_retries=3,
)


@agent.instructions
def add_result_context(ctx: RunContext[ChatDeps]) -> str:
    rs = ctx.deps.session.result_set
    if rs is None:
        return "No active search results yet."
    return rs.describe_for_instructions()


async def run_agent(user_message: str, deps: ChatDeps) -> AgentResult:
    # Model build is cached so HTTPX connection pools persist across requests;
    # tests bypass this entirely via `agent.override(model=TestModel())`.
    result = await agent.run(
        user_message,
        model=build_chat_model(),
        deps=deps,
        message_history=deps.session.message_history,
    )
    return AgentResult(
        output=result.output,
        new_messages=result.new_messages(),
    )
