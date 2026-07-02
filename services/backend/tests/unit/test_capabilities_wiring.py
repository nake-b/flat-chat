"""Capability wiring — the agent surfaces its tools via `capabilities=[...]`.

Tool binding is split across four capabilities — `CoreCapability`,
`MapOverlayCapability`, `LensCapability` (always loaded) and the DEFERRED
`ListingProximityCapability` — each wrapping its own `FunctionToolset` via
`get_toolset()`. This guards that the composition actually reaches the model:
the always-loaded tools must all be present and un-deferred, the proximity tools
must be present and FLAGGED deferred, and Pydantic AI must inject its
`load_capability` + `search_tools` plumbing (proof deferral is active). If a
future refactor drops a capability, flips `defer_loading` on the wrong one, or
breaks `get_toolset()`, this fails.

`get_toolset()` returns the toolset wrapped in `StateEmittingToolset` (the
forget-proof state-emission wrapper) — this also confirms that wrapping a
`WrapperToolset` inside the capability doesn't hide the tools from the model.

Drives the real module-level `agent` with a `FunctionModel` that records the
`AgentInfo.function_tools` it was handed, making no tool calls (so the `None`
services on `ChatDeps` are never touched).
"""

from __future__ import annotations

import asyncio

from pydantic_ai._deferred_capabilities import (
    DEFERRED_CAPABILITY_TOOL_METADATA_KEY,
)
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

# Always-loaded tools (Core / MapOverlay / Lens) — present AND un-deferred.
ALWAYS_LOADED_TOOLS = {
    # CoreCapability
    "search_apartments",
    "open_listing",
    "get_result_page",
    "locate_place",
    # MapOverlayCapability
    "show_on_map",
    "hide_on_map",
    "clear_map_overlays",
    # LensCapability
    "apply_travel_time_lens",
    "apply_distance_lens",
    "clear_lens",
}

# ListingProximityCapability (defer_loading=True) — present AND flagged deferred.
DEFERRED_TOOLS = {"distance_to", "travel_time_to"}

# Plumbing Pydantic AI injects once ANY capability defers: the on-demand loader
# tool + the tool-search catalog tool.
DEFERRED_PLUMBING = {"load_capability", "search_tools"}


def test_agent_advertises_listing_tools_via_capability():
    captured: dict[str, bool] = {}

    def capture_fn(_messages, info: AgentInfo) -> ModelResponse:
        # name → is it flagged as a deferred-capability tool?
        captured.update(
            {
                t.name: bool(
                    (t.metadata or {}).get(DEFERRED_CAPABILITY_TOOL_METADATA_KEY)
                )
                for t in info.function_tools
            }
        )
        return ModelResponse(parts=[TextPart(content="ok")])

    deps = ChatDeps(
        search_service=None,  # type: ignore[arg-type]  # never called (no tool calls)
        listing_service=None,  # type: ignore[arg-type]
        place_service=None,  # type: ignore[arg-type]
        transit_overlay_service=None,  # type: ignore[arg-type]
        routing_service=None,  # type: ignore[arg-type]
        distance_service=None,  # type: ignore[arg-type]
        session=ChatSession(id="t-wiring"),
        state=SessionState(),
    )

    async def run() -> None:
        # This agent sets its model per-run (provider seam), not at
        # construction — so pass the recording model to run() directly.
        await agent.run("hello", deps=deps, model=FunctionModel(capture_fn))

    asyncio.run(run())

    names = set(captured)
    # Exactly the always-loaded tools + the deferred tools + the deferral plumbing.
    assert names == ALWAYS_LOADED_TOOLS | DEFERRED_TOOLS | DEFERRED_PLUMBING
    # Always-loaded tools are NOT flagged deferred.
    assert all(captured[t] is False for t in ALWAYS_LOADED_TOOLS)
    # Proximity tools ARE flagged deferred (kept out of the cached prefix until
    # the model loads the capability on demand).
    assert all(captured[t] is True for t in DEFERRED_TOOLS)


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
        routing_service=None,  # type: ignore[arg-type]
        distance_service=None,  # type: ignore[arg-type]
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
