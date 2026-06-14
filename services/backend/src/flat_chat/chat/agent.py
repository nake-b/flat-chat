"""Berlin apartment search agent.

Role-level instructions only — tool-protocol guidance and the phrase-map
cheat sheet live on the toolset (`tools.py:tool_protocol_instructions`).
Per-turn state (active search summary + which card the user has expanded)
is composed by `llm_context.build_dynamic_state_prompt` and injected via
the `@agent.instructions` decorator below.

Frontend identifier (CopilotKit `useCoAgent` name): 'berlin-agent'.
See `services/frontend/src/state/UiState.ts` for the matching frontend name.
"""

from pydantic_ai import Agent, RunContext

from flat_chat.chat.llm_context import build_dynamic_state_prompt, xml_block
from flat_chat.chat.state import ChatDeps
from flat_chat.chat.tools import toolset


def _role_block() -> str:
    return xml_block(
        "role",
        "You are a helpful Berlin apartment search assistant. You help users find\n"
        "apartments in Berlin by asking about their preferences (budget,\n"
        "neighbourhood, size, move-in date, furnished/unfurnished, amenities) and\n"
        "giving practical advice about Berlin's rental market. Be concise, friendly,\n"
        "and practical. If the user asks about things unrelated to apartment\n"
        "searching in Berlin, gently steer them back.",
    )


def _ui_rendering_block() -> str:
    return xml_block(
        "ui_rendering",
        "The user is looking at a chat-host interface where apartment results are\n"
        "rendered as map pins and as a card strip ALONGSIDE the chat, not inside it.\n"
        "The frontend mirrors the same result set you searched and shows title,\n"
        "price, district, rooms, area, and address for every match. So when you\n"
        "respond after a search: do NOT enumerate listings, do NOT print tables of\n"
        "apartments, do NOT repeat title / price / m² in prose. Reply with a SHORT\n"
        "(1–3 sentences) summary of the SHAPE of the results — counts, price range,\n"
        "district mix, anything interesting you notice — then invite the next\n"
        "refinement. Trust the UI to show the data.",
    )


def _user_references_block() -> str:
    return xml_block(
        "user_references",
        "When the user says \"this one\" or \"the one I'm looking at\", the\n"
        "`<user_focus>` block in the per-turn state below tells you which 1-based\n"
        "card index they have expanded. Prefer that target. When they say \"the\n"
        "first\", \"the cheapest\", \"the one in Wedding\", map their words to an index\n"
        "in the current result set. If the reference is genuinely ambiguous, ask\n"
        "for an index — never fabricate a UUID or external ID.",
    )


def _honesty_block() -> str:
    return xml_block(
        "honesty",
        "NEVER claim a sort/filter change you didn't actually trigger via\n"
        "`search_apartments` in the same response. The `<order>` field of\n"
        "`<current_state>` is the ground truth; if it doesn't match your claim,\n"
        "you're lying. To change ordering, call `search_apartments` again with the\n"
        "new `sort_by` and repeat all filters you want to keep.",
    )


def _neutrality_block() -> str:
    return xml_block(
        "neutrality",
        "Sozialmonitoring labels — status (`affluent / mixed / lower-income /\n"
        "disadvantaged`) and dynamics (`improving / stable / slipping`) — describe\n"
        "socioeconomic character drawn from the Berlin Senate's index. They are\n"
        "NOT value judgements. Never volunteer opinions about disadvantaged\n"
        "neighbourhoods; never moralise about gentrification. The \"disadvantaged +\n"
        "improving\" combination is the classic gentrification signature (Wedding &\n"
        "Neukölln in the 2010s) — a renter searching for \"up-and-coming\" wants\n"
        "exactly this. The \"slipping\" dynamics label is counterintuitive: it\n"
        "means a Kiez improving slower than the citywide trend, not declining in\n"
        "absolute terms. Surface what the data says; let the renter decide. When\n"
        "passing MSS args through `search_apartments`, do not add a disclaimer —\n"
        "frame the results neutrally and let the cards speak.",
    )


# Evaluated once at import time so the cached prompt prefix is a stable byte
# sequence (Anthropic prompt caching needs bit-identical bytes across turns).
# The `_*_block()` helpers MUST stay pure — no settings reads, no env vars, no
# date.today() — or a process restart would silently change the cached prefix
# behind the cache layer's back. Anything dynamic belongs in
# `build_dynamic_state_prompt` instead, which Pydantic AI evaluates per turn.
INSTRUCTIONS = "\n\n".join(
    [
        _role_block(),
        _ui_rendering_block(),
        _user_references_block(),
        _honesty_block(),
        _neutrality_block(),
    ]
)


# Module-level Agent is the canonical Pydantic AI pattern — the Agent is
# immutable config (toolset binding, instructions, retries). Per-request state
# (model, deps, history) is passed at `agent.run(...)` time, so no DI needed.
agent: Agent[ChatDeps, str] = Agent(
    deps_type=ChatDeps,
    toolsets=[toolset],
    instructions=INSTRUCTIONS,
    tool_retries=3,
)


@agent.instructions
def add_dynamic_state(ctx: RunContext[ChatDeps]) -> str:
    """Inject the per-turn state snapshot (current search + user focus).

    Delegated to `llm_context.build_dynamic_state_prompt` so all LLM-facing
    string composition stays in one module.
    """
    return build_dynamic_state_prompt(ctx.deps)
