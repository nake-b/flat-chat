"""HTTP integration tests for the conversation endpoints.

  POST /api/conversations             — create (persists a row, scoped to user)
  GET  /api/conversations/{id}/messages — history reload (ownership-checked)
  GET  /api/conversations/{id}/state    — SessionState snapshot recovery primitive

Approach mirrors test_listings_api.py: one async engine + transaction, a
`DbSessionStore` bound to that connection via savepoints, `get_session_store`
and `get_user_id` overridden, requests driven through the app via ASGITransport,
ROLLBACK on exit. Gated on ``TEST_DATABASE_URL``.
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
from httpx import ASGITransport
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from flat_chat.chat.session_state import SessionState
from flat_chat.chat.sessions import DbSessionStore
from flat_chat.core.dependencies import get_session_store, get_user_id
from flat_chat.listings.context import Marker
from flat_chat.main import app

from ..conftest import DB_REQUIRED, ensure_app_users

pytestmark = DB_REQUIRED

USER_A = "00000000-0000-0000-0000-0000000000aa"
USER_B = "00000000-0000-0000-0000-0000000000bb"


async def _run_http(async_url, body, request_user=USER_A):
    engine = create_async_engine(async_url)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            try:
                await ensure_app_users(conn, USER_A, USER_B)
                factory = async_sessionmaker(
                    bind=conn,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                )
                store = DbSessionStore(factory)
                app.dependency_overrides[get_session_store] = lambda: store
                app.dependency_overrides[get_user_id] = lambda: request_user
                try:
                    transport = ASGITransport(app=app)
                    async with httpx.AsyncClient(
                        transport=transport, base_url="http://test"
                    ) as client:
                        return await body(client, store)
                finally:
                    app.dependency_overrides.pop(get_session_store, None)
                    app.dependency_overrides.pop(get_user_id, None)
            finally:
                await trans.rollback()
    finally:
        await engine.dispose()


def drive(async_url, body, request_user=USER_A):
    return asyncio.run(_run_http(async_url, body, request_user))


def test_create_conversation_returns_id(async_db_url):
    async def body(client, store):
        return await client.post("/api/conversations")

    resp = drive(async_db_url, body)
    assert resp.status_code == 200
    payload = resp.json()
    assert uuid.UUID(payload["id"])  # parses
    assert "created_at" in payload


def test_get_state_default_then_persisted(async_db_url):
    async def body(client, store):
        session = await store.create(USER_A)
        before = await client.get(f"/api/conversations/{session.id}/state")
        # Persist a non-trivial snapshot, then read it back over HTTP.
        session.state = SessionState(
            total_results=2,
            result_markers=[Marker(id="x", lat=52.5, lng=13.4, price_warm_eur=900.0)],
        )
        await store.save(session)
        after = await client.get(f"/api/conversations/{session.id}/state")
        return before, after

    before, after = drive(async_db_url, body)
    assert before.status_code == 200
    assert before.json()["total_results"] == 0  # default empty state
    assert after.status_code == 200
    body_json = after.json()
    assert body_json["total_results"] == 2
    # Columnar marker wire form survives the DB round-trip + HTTP serialization.
    assert body_json["result_markers"]["ids"] == ["x"]


def test_get_messages_projects_user_and_assistant(async_db_url):
    async def body(client, store):
        session = await store.create(USER_A)
        session.message_history = [
            ModelRequest(parts=[UserPromptPart(content="hi there")]),
            ModelResponse(parts=[TextPart(content="hello back")]),
        ]
        await store.save(session)
        return await client.get(f"/api/conversations/{session.id}/messages")

    resp = drive(async_db_url, body)
    assert resp.status_code == 200
    msgs = resp.json()
    assert [(m["role"], m["content"]) for m in msgs] == [
        ("user", "hi there"),
        ("assistant", "hello back"),
    ]


def test_get_messages_includes_tool_calls_and_results(async_db_url):
    # The transcript is the SSOT for tool "finishes" (issue #22): GET /messages
    # carries the tool call (assistant.toolCalls) + its result (role:"tool"),
    # camelCase, so the frontend re-renders the finish on reload.
    async def body(client, store):
        session = await store.create(USER_A)
        session.message_history = [
            ModelRequest(parts=[UserPromptPart(content="2br kreuzberg")]),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="search_apartments",
                        args={"districts": ["Kreuzberg"]},
                        tool_call_id="c1",
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="search_apartments",
                        content="Found 48 listings, most recent first.",
                        tool_call_id="c1",
                    )
                ]
            ),
            ModelResponse(parts=[TextPart(content="Here are 48.")]),
        ]
        await store.save(session)
        return await client.get(f"/api/conversations/{session.id}/messages")

    resp = drive(async_db_url, body)
    assert resp.status_code == 200
    msgs = resp.json()
    tool_calls = [m for m in msgs if m["role"] == "assistant" and m.get("toolCalls")]
    assert tool_calls
    assert tool_calls[0]["toolCalls"][0]["function"]["name"] == "search_apartments"
    tool_results = [m for m in msgs if m["role"] == "tool"]
    assert tool_results
    assert tool_results[0]["toolCallId"] == "c1"
    assert tool_results[0]["content"].startswith("Found 48 listings")


def test_get_messages_collapses_multi_search_to_last(async_db_url):
    # A turn that broadens (0 → 48) must persist only the LAST search finish.
    async def body(client, store):
        session = await store.create(USER_A)
        session.message_history = [
            ModelRequest(parts=[UserPromptPart(content="broaden it")]),
            ModelResponse(
                parts=[ToolCallPart(tool_name="search_apartments", args={}, tool_call_id="c1")]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="search_apartments",
                        content="No apartments found matching those criteria.",
                        tool_call_id="c1",
                    )
                ]
            ),
            ModelResponse(
                parts=[ToolCallPart(tool_name="search_apartments", args={}, tool_call_id="c2")]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="search_apartments",
                        content="Found 48 listings, most recent first.",
                        tool_call_id="c2",
                    )
                ]
            ),
            ModelResponse(parts=[TextPart(content="Broadened to 48.")]),
        ]
        await store.save(session)
        return await client.get(f"/api/conversations/{session.id}/messages")

    resp = drive(async_db_url, body)
    msgs = resp.json()
    by_id = {m["toolCallId"]: m["content"] for m in msgs if m["role"] == "tool"}
    assert by_id["c1"] == ""  # superseded intermediate blanked
    assert by_id["c2"].startswith("Found 48 listings")  # last kept


def test_get_messages_blanks_retry_results(async_db_url):
    # A failed-validation retry must show nothing on reload (mirrors the live
    # _QuietRetryEventStream contract).
    async def body(client, store):
        session = await store.create(USER_A)
        session.message_history = [
            ModelRequest(parts=[UserPromptPart(content="hi")]),
            ModelResponse(
                parts=[ToolCallPart(tool_name="search_apartments", args={}, tool_call_id="c1")]
            ),
            ModelRequest(
                parts=[
                    RetryPromptPart(
                        content="2 validation errors. Fix and retry.",
                        tool_name="search_apartments",
                        tool_call_id="c1",
                    )
                ]
            ),
            ModelResponse(parts=[TextPart(content="ok")]),
        ]
        await store.save(session)
        return await client.get(f"/api/conversations/{session.id}/messages")

    resp = drive(async_db_url, body)
    msgs = resp.json()
    tool_results = [m for m in msgs if m["role"] == "tool"]
    assert tool_results
    assert all(m["content"] == "" for m in tool_results)


def test_get_messages_drops_thinking(async_db_url):
    # "Thinking" is ephemeral — it must not become a persisted transcript message.
    async def body(client, store):
        session = await store.create(USER_A)
        session.message_history = [
            ModelRequest(parts=[UserPromptPart(content="hi")]),
            ModelResponse(
                parts=[ThinkingPart(content="let me think"), TextPart(content="answer")]
            ),
        ]
        await store.save(session)
        return await client.get(f"/api/conversations/{session.id}/messages")

    resp = drive(async_db_url, body)
    msgs = resp.json()
    assert all(m["role"] != "reasoning" for m in msgs)
    assert any(
        m["role"] == "assistant" and m.get("content") == "answer" for m in msgs
    )


def test_unknown_conversation_404(async_db_url):
    async def body(client, store):
        return await client.get(f"/api/conversations/{uuid.uuid4()}/state")

    resp = drive(async_db_url, body)
    assert resp.status_code == 404


def test_foreign_conversation_is_404_not_403(async_db_url):
    """A conversation owned by USER_A is invisible to USER_B (404, no leak)."""

    async def body(client, store):
        # Created as USER_A directly via the store...
        session = await store.create(USER_A)
        # ...but the request is authenticated as USER_B (override below).
        msgs = await client.get(f"/api/conversations/{session.id}/messages")
        state = await client.get(f"/api/conversations/{session.id}/state")
        return msgs, state

    msgs, state = drive(async_db_url, body, request_user=USER_B)
    assert msgs.status_code == 404
    assert state.status_code == 404
