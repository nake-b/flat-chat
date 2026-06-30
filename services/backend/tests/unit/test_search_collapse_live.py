"""Live search-finish collapse, driven end-to-end through the REAL event stream.

`test_retry_suppression.py` exercises `_FlatChatEventStream.transform_stream`
with the base `AGUIEventStream.transform_stream` monkeypatched out — fast, but
it would stay green if a pydantic-ai upgrade changed the base method's name,
signature, or the order/shape of the native events it emits (exactly the
"compiles but doesn't run" gap CLAUDE.md warns about). The collapse leans on a
library-internal override, so the seam needs a test that runs against the real
thing.

These tests drive the full `ChatService.dispatch_agent_request` path with a
streaming `FunctionModel` that emits genuine `search_apartments` tool calls. The
agent runs for real → pydantic-ai emits native events → the real
`_FlatChatAGUIAdapter` wraps them in `_FlatChatEventStream` → we parse the SSE
body and assert the collapse held. No base method is stubbed; if the library
changes under us, these go red.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from starlette.requests import Request

import flat_chat.chat.service as service_mod
from flat_chat.chat.agent import agent
from flat_chat.chat.service import ChatService
from flat_chat.chat.sessions import InMemorySessionStore
from flat_chat.chat.state import ChatDeps

USER = "00000000-0000-0000-0000-000000000001"

ZERO = "No apartments found matching those criteria. Try broadening your search."


def _make_request(envelope: dict) -> Request:
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


def _envelope(thread_id: str) -> dict:
    return {
        "threadId": thread_id,
        "runId": "run-test",
        "state": {},
        "messages": [{"id": "m1", "role": "user", "content": "find me a flat"}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }


def _stub_toolset() -> FunctionToolset[ChatDeps]:
    """A `search_apartments` that just echoes the summary the model planned.

    Real tool name (so the collapse keys on it) + a real ToolReturn (so the
    native result event flows), but no search_service dependency."""
    stub: FunctionToolset[ChatDeps] = FunctionToolset()

    @stub.tool
    async def search_apartments(ctx: RunContext[ChatDeps], summary: str) -> str:
        return summary

    return stub


def _make_stream_fn(plan: list[str]):
    """Emit one `search_apartments` call per step (its arg = the planned summary),
    then a final text answer once every planned search has returned."""

    async def stream_fn(messages, _info):
        done = sum(
            1
            for m in messages
            for p in getattr(m, "parts", [])
            if isinstance(p, ToolReturnPart)
        )
        if done < len(plan):
            yield {
                0: DeltaToolCall(
                    name="search_apartments",
                    json_args=json.dumps({"summary": plan[done]}),
                    tool_call_id=f"c{done + 1}",
                )
            }
        else:
            yield "Here are the results."

    return stream_fn


def _drive(plan: list[str]) -> list[dict]:
    """Run a full dispatch with `plan` searches and return the parsed SSE events."""

    async def go() -> list[dict]:
        store = InMemorySessionStore()
        session = await store.create(USER)
        chat = ChatService(
            search_service=None, listing_service=None, place_service=None, store=store
        )
        original_build = service_mod.build_chat_model
        service_mod.build_chat_model = lambda: FunctionModel(
            stream_function=_make_stream_fn(plan)
        )
        chunks: list = []
        try:
            with agent.override(toolsets=[_stub_toolset()]):
                resp = await chat.dispatch_agent_request(
                    _make_request(_envelope(session.id)), USER
                )
                async for chunk in resp.body_iterator:
                    chunks.append(chunk)
        finally:
            service_mod.build_chat_model = original_build
        return _parse_sse(chunks)

    return asyncio.run(go())


def _parse_sse(chunks: list) -> list[dict]:
    text = "".join(c.decode() if isinstance(c, bytes) else c for c in chunks)
    events: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            with contextlib.suppress(json.JSONDecodeError):
                events.append(json.loads(line[len("data:") :].strip()))
    return events


def _net_contents(events: list[dict]) -> dict[str, str]:
    """Net on-screen content per toolCallId (a later empty result blanks a pill)."""
    net: dict[str, str] = {}
    for e in events:
        if e.get("type") == "TOOL_CALL_RESULT":
            net[e.get("toolCallId")] = e.get("content")
    return net


def _order(events: list[dict]) -> list[tuple]:
    seq: list[tuple] = []
    for e in events:
        t = e.get("type")
        if t == "TOOL_CALL_START":
            seq.append(("start", e.get("toolCallId")))
        elif t == "TOOL_CALL_RESULT":
            seq.append(("result", e.get("toolCallId"), bool(e.get("content"))))
    return seq


def test_live_silent_multi_search_collapses_to_last():
    # search 0 → broaden → 48, no narration between, then answer. Through the REAL
    # event stream: only the last finish keeps content; the superseded pill is
    # resolved EMPTY before the next "Searching…" starts.
    events = _drive([ZERO, "Found 48 listings, most recent first."])
    net = _net_contents(events)
    assert net["c1"] == ""
    assert net["c2"].startswith("Found 48 listings")
    order = _order(events)
    assert order.index(("result", "c1", False)) < order.index(("start", "c2"))


def test_live_repeated_empty_searches_collapse_to_one():
    # The reported #22 case: three identical "No apartments found" must not stack.
    events = _drive([ZERO, ZERO, ZERO])
    on_screen = {cid: c for cid, c in _net_contents(events).items() if c}
    assert list(on_screen) == ["c3"]


def test_live_single_search_passes_through():
    events = _drive(["Found 12 listings, most recent first."])
    net = _net_contents(events)
    assert net["c1"].startswith("Found 12 listings")
