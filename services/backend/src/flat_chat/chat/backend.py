"""The agent seam — the one interface a hackathon team implements.

This file is the contract between the HTTP/SSE plumbing (which you do NOT
need to touch) and *your* agent. `ChatService` parses the incoming AG-UI
request, hydrates `ChatDeps`, brackets your run with `RUN_STARTED` /
`RUN_FINISHED`, SSE-encodes whatever you yield, and persists the session
when you're done. Your job is everything in between: read the user's
message, decide what to do, call the search/listing services, mutate
`deps.state`, and yield AG-UI events so the frontend renders.

To plug in your framework, implement `AgentBackend.run` and wire your
class into `core/dependencies.py:get_chat_service` in place of
`ExampleSearchBackend`. See `chat/example_backend.py` for a complete,
working, no-LLM reference and `HACKATHON.md` for the full guide.

You are NOT locked into Pydantic AI — nothing here imports it. `run` is a
plain async generator of `ag_ui` protocol events. If your framework ships
its own AG-UI adapter you can yield its events straight through; otherwise
build them with the helpers below.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterable
from typing import Any, Protocol

from ag_ui.core import (
    BaseEvent,
    RunAgentInput,
    StateSnapshotEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)

from flat_chat.chat.state import ChatDeps


class AgentBackend(Protocol):
    """The single method a hackathon team implements.

    `run` receives the parsed AG-UI request (`run_input.messages` is the
    full thread; `run_input.state` is the frontend's current state) and the
    request-scoped `deps` (your handles to `search_service`,
    `listing_service`, the session, and the mutable `state`).

    Yield `ag_ui` events as you work. Do NOT emit `RunStartedEvent` /
    `RunFinishedEvent` — `ChatService` brackets your stream with those. A
    typical run yields: a text reply, optionally a tool-call lifecycle
    (so a status pill shows in the UI), and a `StateSnapshotEvent` carrying
    the updated `deps.state` so the map + cards re-render.
    """

    def run(
        self, *, run_input: RunAgentInput, deps: ChatDeps
    ) -> AsyncIterator[BaseEvent]: ...


# --- Helpers --------------------------------------------------------------
# Thin wrappers over the raw ag_ui event classes so a backend can emit the
# common shapes in one call. Use them or hand-roll events — both are fine.


def new_id() -> str:
    """A fresh id for a message or tool call."""
    return uuid.uuid4().hex


def latest_user_text(run_input: RunAgentInput) -> str:
    """The most recent user message as plain text ("" if none / multimodal)."""
    for msg in reversed(run_input.messages):
        if getattr(msg, "role", None) != "user":
            continue
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            return content.strip()
        return ""  # multimodal content — not handled by the example
    return ""


def text_message(text: str, *, message_id: str | None = None) -> list[BaseEvent]:
    """A complete assistant text bubble as START → CONTENT → END."""
    mid = message_id or new_id()
    return [
        TextMessageStartEvent(message_id=mid),
        TextMessageContentEvent(message_id=mid, delta=text),
        TextMessageEndEvent(message_id=mid),
    ]


def tool_call(
    name: str,
    *,
    result: str,
    args: dict[str, Any] | None = None,
    tool_call_id: str | None = None,
) -> list[BaseEvent]:
    """A complete tool-call lifecycle START → ARGS → END → RESULT.

    The frontend maps `name` to a status-pill label via
    `services/frontend/src/state/toolStatus.ts`. Use a name that exists
    there (e.g. `search_apartments`) or add your own entry. `args` is the
    JSON the pill's label reads (e.g. `{"districts": ["Kreuzberg"]}`).
    """
    cid = tool_call_id or new_id()
    events: list[BaseEvent] = [
        ToolCallStartEvent(tool_call_id=cid, tool_call_name=name),
    ]
    if args is not None:
        events.append(ToolCallArgsEvent(tool_call_id=cid, delta=json.dumps(args)))
    events.append(ToolCallEndEvent(tool_call_id=cid))
    events.append(
        ToolCallResultEvent(message_id=new_id(), tool_call_id=cid, content=result)
    )
    return events


def state_snapshot(deps: ChatDeps) -> StateSnapshotEvent:
    """Push the current `deps.state` to the frontend (re-renders map + cards)."""
    return StateSnapshotEvent(snapshot=deps.state.model_dump(mode="json"))


def flatten(*groups: BaseEvent | Iterable[BaseEvent]) -> list[BaseEvent]:
    """Concatenate single events and lists-of-events into one flat list."""
    out: list[BaseEvent] = []
    for g in groups:
        if isinstance(g, BaseEvent):
            out.append(g)
        else:
            out.extend(g)
    return out
