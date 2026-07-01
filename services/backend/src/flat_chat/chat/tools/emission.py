"""Structural state emission for the agent's toolset.

Pydantic AI's AG-UI integration is one-directional by default: it validates
the incoming envelope's `state` onto `deps.state` at run start
(`pydantic_ai/ui/_adapter.py`), but it does NOT push `deps.state` mutations
back to the frontend. The only sanctioned way state reaches the UI mid-run is
a `BaseEvent` placed in a tool's `ToolReturn.metadata`, which the adapter
yields into the SSE stream (`pydantic_ai/ui/ag_ui/_event_stream.py`).

Historically each mutating tool had to remember to wrap its return in a
`StateSnapshotEvent` — a footgun: mutate-but-forget-to-emit left the agent and
the persisted session in sync while the UI went blind. As the number of
state-mutating tools grows (search, open, overlays), that footgun bites.

`StateEmittingToolset` removes it. It wraps the whole toolset and intercepts
every `call_tool`: snapshot `deps.state` before and after the call, and if it
changed, attach a `StateSnapshotEvent` to the result. Tool authors do exactly
one thing — mutate `ctx.deps.state` — and emission is structural, impossible to
forget, because it lives here and not in any tool body. Non-mutating tools
(`locate_place`, `get_result_page`) emit nothing automatically, so they don't
re-ship the (potentially large) marker payload needlessly.

Routing note: when this wrapper is the toolset registered with the agent,
`CombinedToolset.call_tool` dispatches to `tool.source_toolset.call_tool` —
i.e. this wrapper — which then delegates inward via `super().call_tool`. So
interception is reliable for every tool the agent can call.

Upgrade path: emitting a `StateDeltaEvent` (RFC-6902 JSON-Patch of
before→after) instead of a full snapshot is a drop-in change inside
`call_tool`, and is what AG-UI recommends for incremental updates if the
full-snapshot payload ever bites. v1 ships full snapshots for simplicity.
"""

from __future__ import annotations

from typing import Any

from ag_ui.core import BaseEvent, EventType, StateSnapshotEvent
from pydantic_ai import ToolReturn
from pydantic_ai._run_context import RunContext
from pydantic_ai.toolsets import ToolsetTool, WrapperToolset

from flat_chat.chat.state import ChatDeps


class StateEmittingToolset(WrapperToolset[ChatDeps]):
    """Wrap a toolset so any `deps.state` change auto-emits a STATE_SNAPSHOT.

    The before/after comparison uses `model_dump()` on the SessionState. For
    our scale (a handful of tool calls per turn, ≤ MARKER_CAP markers) the
    double dump is cheap relative to the search itself. If it ever shows up in
    a profile, cache the last-emitted dump per run instead.
    """

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[ChatDeps],
        tool: ToolsetTool[ChatDeps],
    ) -> Any:
        before = _dump_state(ctx)
        result = await super().call_tool(name, tool_args, ctx, tool)
        after = _dump_state(ctx)

        if after is None or after == before:
            return result
        return _attach_state_snapshot(result, after)


def _dump_state(ctx: RunContext[ChatDeps]) -> dict[str, Any] | None:
    """Best-effort `SessionState.model_dump()` for the run's deps.

    Returns None when there's no dumpable state (e.g. a deps shape without
    `state`), in which case the caller skips emission entirely.
    """
    state = getattr(getattr(ctx, "deps", None), "state", None)
    dump = getattr(state, "model_dump", None)
    if dump is None:
        return None
    return dump()


def _attach_state_snapshot(result: Any, snapshot: dict[str, Any]) -> ToolReturn:
    """Return a `ToolReturn` carrying a STATE_SNAPSHOT for `snapshot`.

    Preserves a tool's existing `ToolReturn` (return value, content, and any
    metadata events it already set) and only appends the snapshot — unless a
    `StateSnapshotEvent` is already present, in which case the tool emitted its
    own and we leave it untouched (idempotent, no double-emit).
    """
    event = StateSnapshotEvent(type=EventType.STATE_SNAPSHOT, snapshot=snapshot)

    if isinstance(result, ToolReturn):
        metadata = _as_event_list(result.metadata)
        if any(isinstance(e, StateSnapshotEvent) for e in metadata):
            return result
        # Rebuild (rather than mutate) to stay safe across ToolReturn versions;
        # preserves return_value + content, appends the snapshot to metadata.
        return ToolReturn(
            return_value=result.return_value,
            content=result.content,
            metadata=[*metadata, event],
        )

    return ToolReturn(return_value=result, metadata=[event])


def _as_event_list(metadata: Any) -> list[BaseEvent]:
    """Normalise `ToolReturn.metadata` (None | event | iterable) to a list."""
    if metadata is None:
        return []
    if isinstance(metadata, BaseEvent):
        return [metadata]
    if isinstance(metadata, (str, bytes)):
        return []
    try:
        return list(metadata)
    except TypeError:
        return []
