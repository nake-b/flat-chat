"""Capability wiring — the agent surfaces its tools via `capabilities=[...]`.

The Pydantic AI v2 upgrade moved tool binding from `toolsets=[toolset]` to
`capabilities=[ListingsCapability()]` (ListingsCapability wraps the same
`FunctionToolset` via `get_toolset()`). This guards that the indirection
actually reaches the model: the agent must still advertise exactly the three
tools to the LLM. If a future refactor drops the capability, marks it
`defer_loading=True` by accident, or breaks `get_toolset()`, this fails.

Drives the real module-level `agent` with a `FunctionModel` that records the
`AgentInfo.function_tools` it was handed, making no tool calls (so the `None`
services on `ChatDeps` are never touched).
"""

from __future__ import annotations

import asyncio

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from flat_chat.chat.agent import agent
from flat_chat.chat.session_state import SessionState
from flat_chat.chat.state import ChatDeps, ChatSession

EXPECTED_TOOLS = {
    "search_apartments",
    "open_listing",
    "get_result_page",
    "locate_place",
}


def test_agent_advertises_listing_tools_via_capability():
    captured: dict[str, set[str]] = {}

    def capture_fn(_messages, info: AgentInfo) -> ModelResponse:
        captured["tools"] = {t.name for t in info.function_tools}
        return ModelResponse(parts=[TextPart(content="ok")])

    deps = ChatDeps(
        search_service=None,  # type: ignore[arg-type]  # never called (no tool calls)
        listing_service=None,  # type: ignore[arg-type]
        place_service=None,  # type: ignore[arg-type]
        session=ChatSession(id="t-wiring"),
        state=SessionState(),
    )

    async def run() -> None:
        # This agent sets its model per-run (provider seam), not at
        # construction — so pass the recording model to run() directly.
        await agent.run("hello", deps=deps, model=FunctionModel(capture_fn))

    asyncio.run(run())
    assert captured["tools"] == EXPECTED_TOOLS
