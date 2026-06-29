"""Capability wiring — the agent surfaces its tools via `capabilities=[...]`.

The Pydantic AI v2 upgrade moved tool binding from `toolsets=[toolset]` to
`capabilities=[ListingsCapability()]` (ListingsCapability wraps the same
`FunctionToolset` via `get_toolset()`). This guards that the indirection
actually reaches the model: the agent must still advertise exactly its tool
set to the LLM. If a future refactor drops the capability, marks it
`defer_loading=True` by accident, or breaks `get_toolset()`, this fails.

`get_toolset()` returns the toolset wrapped in `StateEmittingToolset` (the
forget-proof state-emission wrapper) — this also confirms that wrapping a
`WrapperToolset` inside the capability doesn't hide the tools from the model.

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
from flat_chat.listings.labels import describe_distance_ladder
from flat_chat.listings.thresholds import (
    DENSITY_MODERATE_MAX,
    DENSITY_SPARSE_MAX,
    GREENERY_BUFFER_M,
    NOISE_LIVELY_MAX_LDEN,
    NOISE_QUIET_MAX_LDEN,
)

EXPECTED_TOOLS = {
    "search_apartments",
    "open_listing",
    "get_result_page",
    "locate_place",
    "show_on_map",
    "hide_on_map",
    "clear_map_overlays",
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
        transit_overlay_service=None,  # type: ignore[arg-type]
        session=ChatSession(id="t-wiring"),
        state=SessionState(),
    )

    async def run() -> None:
        # This agent sets its model per-run (provider seam), not at
        # construction — so pass the recording model to run() directly.
        await agent.run("hello", deps=deps, model=FunctionModel(capture_fn))

    asyncio.run(run())
    assert captured["tools"] == EXPECTED_TOOLS


def _capture_search_param_docs() -> dict[str, str]:
    """Drive the agent; return `search_apartments`' per-parameter descriptions.

    The threshold prose lives in the parameter descriptions inside
    `parameters_json_schema` (griffe lifts the `Args:` bullets there), not the
    top-level tool `description`. Reading the dict gives the text un-escaped, so
    the single-line ladder matches verbatim.
    """
    captured: dict[str, str] = {}

    def capture_fn(_messages, info: AgentInfo) -> ModelResponse:
        for t in info.function_tools:
            if t.name == "search_apartments":
                props = t.parameters_json_schema.get("properties", {})
                for name, spec in props.items():
                    captured[name] = spec.get("description", "")
        return ModelResponse(parts=[TextPart(content="ok")])

    deps = ChatDeps(
        search_service=None,  # type: ignore[arg-type]
        listing_service=None,  # type: ignore[arg-type]
        place_service=None,  # type: ignore[arg-type]
        transit_overlay_service=None,  # type: ignore[arg-type]
        session=ChatSession(id="t-doc"),
        state=SessionState(),
    )

    async def run() -> None:
        await agent.run("hello", deps=deps, model=FunctionModel(capture_fn))

    asyncio.run(run())
    return captured


def test_search_tool_docs_generated_from_thresholds():
    """The LLM-facing distance/noise/greenery/density numbers come from constants.

    Guards the `{{TOKEN}}` → `render_threshold_tokens` injection that runs
    before griffe parses the docstring. If the injection breaks (sentinel left
    raw, decorator restored so the render is skipped, or a number re-hardcoded
    out of sync), this fails. The drift can't happen the other way: the
    asserted values are read from `thresholds.py`, the same source the prompt
    renders from.
    """
    params = _capture_search_param_docs()
    everything = "\n".join(params.values())

    # No un-substituted sentinel leaked into the prompt the model receives.
    assert "{{" not in everything

    # The generated ladder is present verbatim in `transit` (proves render ran).
    assert describe_distance_ladder() in params["transit"]

    # The scalar cutoffs are present, each read from the constants.
    assert f"< {int(NOISE_QUIET_MAX_LDEN)} dB" in params["max_noise"]
    assert f"< {int(NOISE_LIVELY_MAX_LDEN)} dB" in params["max_noise"]
    assert f"<{int(DENSITY_SPARSE_MAX)} persons/ha" in params["density"]
    assert f"≥{int(DENSITY_MODERATE_MAX)}" in params["density"]
    assert f"{int(GREENERY_BUFFER_M)}m = leafy" in params["min_greenery"]
