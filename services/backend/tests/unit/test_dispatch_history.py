"""Server-authoritative dispatch — the stored history is the source of truth.

`ChatService.dispatch_agent_request` ALWAYS injects the stored conversation
history as `message_history`; the AG-UI adapter's `messages` is overridden to
contribute only THIS turn's new user input (`_new_turn_messages`). So the agent
always sees `stored_history + new_turn`, regardless of what thread the client
echoes back — no `len(messages)` heuristic, and a stale/tampered/filtered client
thread can't diverge the agent's context.

These tests drive the real dispatch path with an `InMemorySessionStore` (no DB)
and a streaming `FunctionModel` that records the messages the agent actually
received, then assert what the model saw:

  - reload (client sends only the new prompt) → stored history + prompt
  - live (client echoes the full thread)      → no duplication (same result)
  - divergent client thread                   → DB wins; client thread ignored
  - first turn (no stored history)            → just the new prompt
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from starlette.requests import Request

import flat_chat.chat.service as service_mod
from flat_chat.chat.service import ChatService
from flat_chat.chat.sessions import InMemorySessionStore, SessionNotFoundError

USER = "00000000-0000-0000-0000-000000000001"
OTHER_USER = "00000000-0000-0000-0000-0000000000bb"


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
        routing_service=None,
        distance_service=None,
        store=store,
    )
    # Force the run to use our recording model — bypasses provider selection
    # (no API keys in the test env) and lets us inspect what the agent saw.
    original_build = service_mod.build_chat_model
    service_mod.build_chat_model = lambda: FunctionModel(stream_function=stream_fn)
    try:
        resp = await chat.dispatch_agent_request(
            _make_request(_envelope(session.id, envelope_messages)), USER
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
    """Full thread in the envelope → only its new-turn tail is used (+ DB history),
    so each prompt appears exactly once — no doubling of the prior turn."""
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
    assert seen == ["2 rooms in Kreuzberg", "Found 3.", "under 1000 euros"]


def test_divergent_client_thread_is_ignored_db_wins():
    """Server-authoritative: if the client echoes a stale/tampered thread, the DB
    history wins and only the client's NEW user turn is taken from the envelope."""
    seen = asyncio.run(
        _messages_seen_by_model(
            stored_history=_prior_turn(),  # DB truth: "2 rooms…", "Found 3."
            envelope_messages=[
                {"id": "x", "role": "user", "content": "TAMPERED prompt"},
                {"id": "y", "role": "assistant", "content": "fabricated reply"},
                {"id": "z", "role": "user", "content": "under 1000 euros"},
            ],
        )
    )
    # The tampered prefix is dropped; agent sees DB history + only the new turn.
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


def test_foreign_session_is_rejected_before_run():
    """A conversation owned by USER is invisible to OTHER_USER over /api/agent.

    Mirrors the REST `_load_owned` 404-not-403 contract: dispatch raises
    SessionNotFoundError (→ 404 at the route) when the authenticated user_id
    doesn't own the thread, BEFORE any model is built or run.
    """

    async def body() -> None:
        store = InMemorySessionStore()
        session = await store.create(USER)
        chat = ChatService(
            search_service=None,
            listing_service=None,
            place_service=None,
            transit_overlay_service=None,
            routing_service=None,
            distance_service=None,
            store=store,
        )
        # build_chat_model must never be reached — the gate is before it.
        with pytest.raises(SessionNotFoundError):
            await chat.dispatch_agent_request(
                _make_request(
                    _envelope(
                        session.id,
                        [{"id": "m1", "role": "user", "content": "hi"}],
                    )
                ),
                OTHER_USER,
            )

    asyncio.run(body())
