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
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from flat_chat.chat.session_state import SessionState
from flat_chat.chat.sessions import DbSessionStore
from flat_chat.core.dependencies import get_session_store, get_user_id
from flat_chat.listings.context import Marker
from flat_chat.main import app

from ..conftest import DB_REQUIRED

pytestmark = DB_REQUIRED

USER_A = "00000000-0000-0000-0000-0000000000aa"
USER_B = "00000000-0000-0000-0000-0000000000bb"


async def _run_http(async_url, body, request_user=USER_A):
    engine = create_async_engine(async_url)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            try:
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
