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
from flat_chat.listings.thresholds import (
    BUCKET_TO_METERS,
    DENSITY_MODERATE_MAX,
    DENSITY_SPARSE_MAX,
    GREENERY_BUFFER_M,
    GREENERY_LEAFY_MIN_M2,
    GREENERY_VERY_LEAFY_MIN_M2,
    NOISE_LIVELY_MAX_LDEN,
    NOISE_QUIET_MAX_LDEN,
)

_SQM_PER_HECTARE = 10_000

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


def _fmt(value: float) -> str:
    """Render a threshold the way the docstring writes it: integers shed the
    trailing `.0` (`55.0` → `"55"`), fractions are kept (`0.5` → `"0.5"`).

    Using `:g` rather than `int()` is deliberate — `int()` would truncate, so a
    fractional threshold (say `52.5` dB) would silently assert `< 52 dB` and
    *bless* a docstring that misstates the cutoff. `:g` yields `"52.5"`, which
    won't match the stale `< 55 dB` prose, surfacing the drift instead.
    """
    return f"{value:g}"


def _capture_search_param_docs() -> dict[str, str]:
    """Drive the agent; return `search_apartments`' per-parameter descriptions.

    The threshold prose lives in the parameter descriptions inside
    `parameters_json_schema` (griffe lifts the `Args:` bullets there), not the
    top-level tool `description`. Reading the dict gives the text un-escaped.
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


def test_search_tool_docs_match_thresholds():
    """The LLM-facing distance/noise/greenery/density numbers match the constants.

    These numbers are written out literally in the `search_apartments` docstring
    (see the note in `chat/tools.py`). `thresholds.py` is the single source of
    truth the SQL filters read, so this guards that the prose can't silently
    drift from it: tuning a constant without updating the docstring fails here.
    Expected values are read from `thresholds.py`, so the assertions track the
    source automatically.
    """
    params = _capture_search_param_docs()

    # Every distance bucket's metres appears in the `transit` ladder.
    for meters in BUCKET_TO_METERS.values():
        assert f"≤{meters}m" in params["transit"]

    # The scalar cutoffs are present, each read from the constants. `_fmt`
    # (not `int()`) so a fractional threshold can't truncate into a stale match.
    assert f"< {_fmt(NOISE_QUIET_MAX_LDEN)} dB" in params["max_noise"]
    assert f"< {_fmt(NOISE_LIVELY_MAX_LDEN)} dB" in params["max_noise"]
    assert f"<{_fmt(DENSITY_SPARSE_MAX)} persons/ha" in params["density"]
    assert f"≥{_fmt(DENSITY_MODERATE_MAX)}" in params["density"]

    # Greenery: both the 300m window AND the two area cutoffs (in hectares, as
    # the WHO-rule prose phrases them) are guarded — the area thresholds were
    # previously hardcoded as `≥0.5 ha`/`≥1 ha` with nothing tying them back.
    leafy_ha = _fmt(GREENERY_LEAFY_MIN_M2 / _SQM_PER_HECTARE)
    very_leafy_ha = _fmt(GREENERY_VERY_LEAFY_MIN_M2 / _SQM_PER_HECTARE)
    assert f"{GREENERY_BUFFER_M}m = leafy" in params["min_greenery"]
    assert f"≥{leafy_ha} ha" in params["min_greenery"]
    assert (
        f"≥{very_leafy_ha} ha within {GREENERY_BUFFER_M}m = very_leafy"
        in params["min_greenery"]
    )
