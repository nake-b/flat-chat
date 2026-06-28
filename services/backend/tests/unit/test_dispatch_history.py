"""History-authoritative dispatch — the reload-recovery injection logic.

`ChatService.dispatch_agent_request` injects the stored conversation history
into the agent run ONLY when the frontend sent ≤1 envelope message (the reload
case where the transcript wasn't restored, so the client carries just the new
prompt). In a normal live turn the envelope already carries the full thread, so
we inject nothing — passing stored history too would duplicate it.

These tests drive the real dispatch path with an `InMemorySessionStore` (no DB)
and a streaming `FunctionModel` that records the messages the agent actually
received, then assert what the model saw:

  - reload (1 msg)  → stored history is prepended (agent keeps context)
  - live (full thread) → no duplication (model sees exactly the envelope)
  - first turn (no stored history) → just the new prompt

This locks in the `len(adapter.messages) <= 1` branch in service.py, which was
previously only covered by a manual end-to-end check.
"""

from __future__ import annotations

import asyncio
import json

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from starlette.requests import Request

import flat_chat.chat.service as service_mod
from flat_chat.chat.service import ChatService
from flat_chat.chat.sessions import InMemorySessionStore

USER = "00000000-0000-0000-0000-000000000001"


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


def _envelope(thread_id: str, messages: list[dict]) -> dict:
    # AG-UI RunAgentInput (camelCase aliases); all top-level fields are required.
    return {
        "threadId": thread_id,
        "runId": "run-test",
        "state": {},
        "messages": messages,
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }


async def _messages_seen_by_model(
    stored_history: list,
    envelope_messages: list[dict],
) -> list[str]:
    """Run dispatch once and return the user/assistant text the model received."""
    captured: dict[str, list] = {}

    async def stream_fn(messages, _info: AgentInfo):
        captured["messages"] = messages
        yield "done"

    store = InMemorySessionStore()
    session = await store.create(USER)
    session.message_history = stored_history

    chat = ChatService(
        search_service=None,
        listing_service=None,
        place_service=None,
        transit_overlay_service=None,
        store=store,
    )
    # Force the run to use our recording model — bypasses provider selection
    # (no API keys in the test env) and lets us inspect what the agent saw.
    original_build = service_mod.build_chat_model
    service_mod.build_chat_model = lambda: FunctionModel(stream_function=stream_fn)
    try:
        resp = await chat.dispatch_agent_request(
            _make_request(_envelope(session.id, envelope_messages))
        )
        async for _ in resp.body_iterator:  # drive the agent to completion
            pass
    finally:
        service_mod.build_chat_model = original_build

    return [
        part.content
        for msg in captured["messages"]
        for part in msg.parts
        if isinstance(part, (UserPromptPart, TextPart))
        and isinstance(part.content, str)
    ]


def _prior_turn() -> list:
    return [
        ModelRequest(parts=[UserPromptPart(content="2 rooms in Kreuzberg")]),
        ModelResponse(parts=[TextPart(content="Found 3.")]),
    ]


def test_reload_injects_stored_history():
    """≤1 envelope message + stored history → history is prepended."""
    seen = asyncio.run(
        _messages_seen_by_model(
            stored_history=_prior_turn(),
            envelope_messages=[
                {"id": "m1", "role": "user", "content": "under 1000 euros"}
            ],
        )
    )
    assert seen == ["2 rooms in Kreuzberg", "Found 3.", "under 1000 euros"]


def test_live_turn_does_not_duplicate_history():
    """Full thread in the envelope → stored history is NOT re-prepended."""
    seen = asyncio.run(
        _messages_seen_by_model(
            stored_history=_prior_turn(),
            envelope_messages=[
                {"id": "a", "role": "user", "content": "2 rooms in Kreuzberg"},
                {"id": "b", "role": "assistant", "content": "Found 3."},
                {"id": "c", "role": "user", "content": "under 1000 euros"},
            ],
        )
    )
    # Each prompt appears exactly once — no doubling of the prior turn.
    assert seen == ["2 rooms in Kreuzberg", "Found 3.", "under 1000 euros"]


def test_first_turn_has_no_history_to_inject():
    """Empty stored history + new prompt → model sees only the new prompt."""
    seen = asyncio.run(
        _messages_seen_by_model(
            stored_history=[],
            envelope_messages=[
                {"id": "m1", "role": "user", "content": "2 rooms in Kreuzberg"}
            ],
        )
    )
    assert seen == ["2 rooms in Kreuzberg"]
