"""StateEmittingToolset — the forget-proof state-emission guarantee.

The wrapper intercepts every `call_tool` and auto-emits a STATE_SNAPSHOT iff
the tool changed `ctx.deps.state`. These tests lock in that contract directly
at the wrapper level (no LLM, no DB):

  - a tool that MUTATES state → result carries exactly one StateSnapshotEvent
    whose snapshot equals the post-call state dump
  - a tool that does NOT mutate state → result passes through untouched (no
    event, no needless re-ship of the marker payload)
  - a tool that already emitted its own snapshot → not double-emitted
  - an existing ToolReturn (content/metadata) is preserved, snapshot appended

This is the regression guard for footgun #1: if a future refactor breaks
interception, these fail.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from ag_ui.core import EventType, StateSnapshotEvent
from pydantic_ai import ToolReturn

from flat_chat.chat.session_state import SessionState
from flat_chat.chat.state_emission import StateEmittingToolset


class _FakeWrapped:
    """Minimal stand-in for the wrapped toolset: its `call_tool` just runs the
    given coroutine fn against ctx. `StateEmittingToolset.call_tool` calls
    `super().call_tool` → `self.wrapped.call_tool`, so this is all the wrapper
    touches — no FunctionToolset/get_tools plumbing needed for a unit test."""

    def __init__(self, fn):
        self.fn = fn

    async def call_tool(self, name, tool_args, ctx, tool) -> Any:
        return await self.fn(ctx)


def _run(coro):
    return asyncio.run(coro)


async def _call(tool_fn, *, state: SessionState) -> Any:
    wrapped = StateEmittingToolset(_FakeWrapped(tool_fn))  # type: ignore[arg-type]
    ctx = SimpleNamespace(deps=SimpleNamespace(state=state))
    return await wrapped.call_tool("t", {}, ctx, None)  # type: ignore[arg-type]


def test_emits_snapshot_when_tool_mutates_state():
    state = SessionState()

    async def mutate(ctx) -> str:
        ctx.deps.state.total_results = 7
        return "did a thing"

    result = _run(_call(mutate, state=state))

    assert isinstance(result, ToolReturn)
    assert result.return_value == "did a thing"
    events = [e for e in (result.metadata or []) if isinstance(e, StateSnapshotEvent)]
    assert len(events) == 1
    assert events[0].type == EventType.STATE_SNAPSHOT
    # Snapshot reflects the post-call state.
    assert events[0].snapshot["total_results"] == 7


def test_no_snapshot_when_tool_does_not_mutate_state():
    state = SessionState()
    state.total_results = 3  # pre-existing — must be left alone

    async def readonly(ctx) -> str:
        _ = ctx.deps.state.total_results  # read only
        return "just looked"

    result = _run(_call(readonly, state=state))

    # Untouched: the plain string passes through, no ToolReturn wrapping.
    assert result == "just looked"


def test_does_not_double_emit_if_tool_already_snapshotted():
    state = SessionState()
    own = StateSnapshotEvent(
        type=EventType.STATE_SNAPSHOT, snapshot={"total_results": 1}
    )

    async def mutate_and_emit(ctx) -> ToolReturn:
        ctx.deps.state.total_results = 99
        return ToolReturn(return_value="x", metadata=[own])

    result = _run(_call(mutate_and_emit, state=state))

    assert isinstance(result, ToolReturn)
    events = [e for e in result.metadata if isinstance(e, StateSnapshotEvent)]
    assert events == [own]  # exactly the tool's own, none added


def test_preserves_existing_tool_return_and_appends_snapshot():
    state = SessionState()
    sentinel = object()

    async def mutate_with_content(ctx) -> ToolReturn:
        ctx.deps.state.total_results = 5
        return ToolReturn(return_value="val", content=["hi"], metadata=[sentinel])

    result = _run(_call(mutate_with_content, state=state))

    assert isinstance(result, ToolReturn)
    assert result.return_value == "val"
    assert result.content == ["hi"]
    # The pre-existing metadata item survives; the snapshot is appended.
    assert sentinel in result.metadata
    snapshots = [e for e in result.metadata if isinstance(e, StateSnapshotEvent)]
    assert len(snapshots) == 1


# ---------------------------------------------------------------------------
# End-to-end: the wrapper's snapshot actually reaches the AG-UI SSE stream.
# This is the integration that the wrapper exists for — it proves the adapter
# routes tool calls through StateEmittingToolset (via CombinedToolset's
# source_toolset dispatch) and yields the StateSnapshotEvent into the stream.
# ---------------------------------------------------------------------------

import json  # noqa: E402

from pydantic_ai.messages import ToolReturnPart  # noqa: E402
from pydantic_ai.models.function import (  # noqa: E402
    AgentInfo,
    DeltaToolCall,
    FunctionModel,
)
from starlette.requests import Request  # noqa: E402

import flat_chat.chat.service as service_mod  # noqa: E402
from flat_chat.chat.service import ChatService  # noqa: E402
from flat_chat.chat.sessions import InMemorySessionStore  # noqa: E402
from flat_chat.listings.context import ListingCard, Marker  # noqa: E402

_USER = "00000000-0000-0000-0000-000000000001"


class _MockSearch:
    async def search(self, params):
        markers = [Marker(id="x1", lat=52.5, lng=13.4, channel_value=1000.0)]
        preview = [ListingCard(id="x1", title="Apt", lat=52.5, lng=13.4)]
        return markers, preview, 1


def _request(envelope: dict) -> Request:
    body = json.dumps(envelope).encode()

    async def receive() -> dict:
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/agent",
        "headers": [(b"content-type", b"application/json")],
        "query_string": b"",
    }
    return Request(scope, receive)


def test_state_snapshot_reaches_sse_stream_end_to_end():
    """A tool that mutates state → a STATE_SNAPSHOT event in the SSE bytes,
    with NO per-tool emit code (the wrapper did it)."""

    async def stream_fn(messages: list, info: AgentInfo):
        # If the search tool already ran (its result is in the history), finish
        # with text; otherwise call search_apartments.
        already_ran = any(
            isinstance(p, ToolReturnPart) and p.tool_name == "search_apartments"
            for m in messages
            for p in getattr(m, "parts", [])
        )
        if already_ran:
            yield "Found some."
        else:
            yield {0: DeltaToolCall(name="search_apartments", json_args="{}")}

    async def drive() -> str:
        store = InMemorySessionStore()
        session = await store.create(_USER)
        chat = ChatService(
            search_service=_MockSearch(),
            listing_service=None,
            place_service=None,
            transit_route_service=None,
            routing_service=None,
            store=store,
        )
        original = service_mod.build_chat_model
        service_mod.build_chat_model = lambda: FunctionModel(stream_function=stream_fn)
        try:
            envelope = {
                "threadId": session.id,
                "runId": "run-test",
                "state": {},
                "messages": [{"id": "m1", "role": "user", "content": "2 rooms"}],
                "tools": [],
                "context": [],
                "forwardedProps": {},
            }
            resp = await chat.dispatch_agent_request(_request(envelope))
            chunks = [chunk async for chunk in resp.body_iterator]
        finally:
            service_mod.build_chat_model = original
        return "".join(c.decode() if isinstance(c, bytes) else c for c in chunks)

    body = _run(drive())
    assert "STATE_SNAPSHOT" in body
