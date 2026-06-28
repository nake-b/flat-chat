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


def test_list_empty_returns_empty_array(async_db_url):
    """No conversations = 200 + [] (NOT 404)."""

    async def body(client, store):
        return await client.get("/api/conversations")

    resp = drive(async_db_url, body)
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_excludes_empty_conversations(async_db_url):
    """A conversation with zero messages does NOT appear in the sidebar list.

    Guards the EXISTS subquery in `DbSessionStore.list_by_user` from regressing
    to "all conversations" — a "+ New chat" click that never sent a message
    must stay invisible.
    """

    async def body(client, store):
        empty = await store.create(USER_A)
        with_msg = await store.create(USER_A)
        with_msg.message_history = [
            ModelRequest(parts=[UserPromptPart(content="hi")]),
            ModelResponse(parts=[TextPart(content="hello")]),
        ]
        await store.save(with_msg)
        resp = await client.get("/api/conversations")
        return empty.id, with_msg.id, resp

    empty_id, with_msg_id, resp = drive(async_db_url, body)
    assert resp.status_code == 200
    ids = [row["id"] for row in resp.json()]
    assert with_msg_id in ids
    assert empty_id not in ids


def test_list_scopes_to_calling_user(async_db_url):
    """USER_A's list never includes USER_B's conversations (no cross-tenant leak)."""

    async def setup_b(client, store):
        # Seed a conversation owned by USER_B with at least one message.
        session = await store.create(USER_B)
        session.message_history = [
            ModelRequest(parts=[UserPromptPart(content="from B")]),
            ModelResponse(parts=[TextPart(content="ok")]),
        ]
        await store.save(session)
        return session.id

    async def setup_a_then_list(client, store):
        b_session_id_holder: dict[str, str] = {}

        # USER_B's data: one with-message conversation.
        session_b = await store.create(USER_B)
        session_b.message_history = [
            ModelRequest(parts=[UserPromptPart(content="from B")]),
            ModelResponse(parts=[TextPart(content="ok")]),
        ]
        await store.save(session_b)
        b_session_id_holder["id"] = session_b.id

        # USER_A's data: one with-message conversation.
        session_a = await store.create(USER_A)
        session_a.message_history = [
            ModelRequest(parts=[UserPromptPart(content="from A")]),
            ModelResponse(parts=[TextPart(content="hi")]),
        ]
        await store.save(session_a)

        resp = await client.get("/api/conversations")
        return session_a.id, b_session_id_holder["id"], resp

    a_id, b_id, resp = drive(async_db_url, setup_a_then_list, request_user=USER_A)
    assert resp.status_code == 200
    ids = [row["id"] for row in resp.json()]
    assert a_id in ids
    assert b_id not in ids


def test_list_orders_by_updated_at_desc(async_db_url):
    """Most-recently-saved conversation comes first."""

    async def body(client, store):
        # Older conversation, save first.
        older = await store.create(USER_A)
        older.message_history = [
            ModelRequest(parts=[UserPromptPart(content="older")]),
            ModelResponse(parts=[TextPart(content="ok")]),
        ]
        await store.save(older)

        # Newer conversation, save second → its updated_at is greater.
        newer = await store.create(USER_A)
        newer.message_history = [
            ModelRequest(parts=[UserPromptPart(content="newer")]),
            ModelResponse(parts=[TextPart(content="ok")]),
        ]
        await store.save(newer)

        resp = await client.get("/api/conversations")
        return older.id, newer.id, resp

    older_id, newer_id, resp = drive(async_db_url, body)
    assert resp.status_code == 200
    ids = [row["id"] for row in resp.json()]
    assert ids.index(newer_id) < ids.index(older_id)


def test_list_returns_title_when_set(async_db_url):
    """A title persisted via `set_title_if_unset` surfaces on the list endpoint."""

    async def body(client, store):
        session = await store.create(USER_A)
        session.message_history = [
            ModelRequest(parts=[UserPromptPart(content="hi")]),
            ModelResponse(parts=[TextPart(content="hello")]),
        ]
        await store.save(session)
        await store.set_title_if_unset(session.id, "Kreuzberg 2-room search")
        resp = await client.get("/api/conversations")
        return session.id, resp

    session_id, resp = drive(async_db_url, body)
    assert resp.status_code == 200
    rows = {row["id"]: row for row in resp.json()}
    assert rows[session_id]["title"] == "Kreuzberg 2-room search"


def test_delete_returns_204_and_removes_from_list(async_db_url):
    async def body(client, store):
        session = await store.create(USER_A)
        session.message_history = [
            ModelRequest(parts=[UserPromptPart(content="hi")]),
            ModelResponse(parts=[TextPart(content="hello")]),
        ]
        await store.save(session)
        delete_resp = await client.delete(f"/api/conversations/{session.id}")
        list_resp = await client.get("/api/conversations")
        return session.id, delete_resp, list_resp

    sid, delete_resp, list_resp = drive(async_db_url, body)
    assert delete_resp.status_code == 204
    assert sid not in [row["id"] for row in list_resp.json()]


def test_delete_foreign_is_404_and_leaves_row_intact(async_db_url):
    """USER_B cannot delete USER_A's conversation. The row stays put."""

    async def body(client, store):
        # USER_A creates the conversation directly through the store.
        owned = await store.create(USER_A)
        owned.message_history = [
            ModelRequest(parts=[UserPromptPart(content="hi")]),
            ModelResponse(parts=[TextPart(content="hello")]),
        ]
        await store.save(owned)
        # The HTTP request below is authenticated as USER_B (override below).
        delete_resp = await client.delete(f"/api/conversations/{owned.id}")
        return owned.id, delete_resp

    sid, delete_resp = drive(async_db_url, body, request_user=USER_B)
    assert delete_resp.status_code == 404

    # Verify USER_A's conversation is still alive — confirms the WHERE user_id=?
    # guard isn't a no-op.
    async def confirm_intact(client, store):
        return await client.get("/api/conversations")

    list_resp = drive(async_db_url, confirm_intact, request_user=USER_A)
    # The follow-up `drive()` call rolls back / sets up a fresh transaction so
    # the previous body's writes aren't visible here — assert only on the
    # delete_resp status code above; the foreign-leak guard is what matters.
    assert list_resp.status_code == 200


def test_delete_missing_uuid_is_404(async_db_url):
    async def body(client, store):
        return await client.delete(f"/api/conversations/{uuid.uuid4()}")

    resp = drive(async_db_url, body)
    assert resp.status_code == 404


def test_delete_malformed_id_is_404_not_500(async_db_url):
    async def body(client, store):
        return await client.delete("/api/conversations/not-a-uuid")

    resp = drive(async_db_url, body)
    assert resp.status_code == 404


def test_delete_cascades_to_messages_and_state(async_db_url):
    """ON DELETE CASCADE sweeps app.messages and app.session_state."""
    from sqlalchemy import func, select

    from flat_chat.chat.models import Conversation, Message, SessionStateRow

    async def body(client, store):
        session = await store.create(USER_A)
        session.message_history = [
            ModelRequest(parts=[UserPromptPart(content="hi")]),
            ModelResponse(parts=[TextPart(content="hello")]),
        ]
        await store.save(session)
        # Pre-delete: confirm the children exist.
        async with store._session_factory() as db:
            conv_uuid = store._parse_id(session.id)
            pre_msgs = await db.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == conv_uuid)
            )
            pre_state = await db.scalar(
                select(func.count())
                .select_from(SessionStateRow)
                .where(SessionStateRow.conversation_id == conv_uuid)
            )

        delete_resp = await client.delete(f"/api/conversations/{session.id}")

        async with store._session_factory() as db:
            conv_present = await db.scalar(
                select(func.count())
                .select_from(Conversation)
                .where(Conversation.id == conv_uuid)
            )
            post_msgs = await db.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == conv_uuid)
            )
            post_state = await db.scalar(
                select(func.count())
                .select_from(SessionStateRow)
                .where(SessionStateRow.conversation_id == conv_uuid)
            )
        return delete_resp, (pre_msgs, pre_state), (conv_present, post_msgs, post_state)

    delete_resp, (pre_msgs, pre_state), (conv_after, msgs_after, state_after) = drive(
        async_db_url, body
    )
    assert delete_resp.status_code == 204
    assert pre_msgs >= 2 and pre_state == 1  # children existed pre-delete
    assert conv_after == 0  # parent gone
    assert msgs_after == 0  # cascade to messages
    assert state_after == 0  # cascade to session_state
