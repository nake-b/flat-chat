"""Integration tests for `DbSessionStore` against real Postgres.

Drives the store through a connection bound with
``join_transaction_mode="create_savepoint"`` so the store's own
``async with db.begin()`` commits become savepoint releases and the outer
``ROLLBACK`` discards everything — keeping the test DB pristine.

Covers the contract the in-memory store can't: history round-trips through
JSONB, the snapshot survives (incl. the columnar marker serializer), messages
append across turns, and a shrinking history triggers the full-rewrite guard.

Gated on ``TEST_DATABASE_URL`` (see tests/README.md).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from flat_chat.chat.session_state import SessionState
from flat_chat.chat.sessions import DbSessionStore, SessionNotFoundError
from flat_chat.listings.context import Marker

from ..conftest import DB_REQUIRED

pytestmark = DB_REQUIRED

USER = "00000000-0000-0000-0000-000000000001"


async def _run_with_store(async_url, body):
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
                return await body(DbSessionStore(factory))
            finally:
                await trans.rollback()
    finally:
        await engine.dispose()


def drive(async_url, body):
    return asyncio.run(_run_with_store(async_url, body))


def _user(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _assistant(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])


def _texts(history) -> list[str]:
    out = []
    for msg in history:
        for part in msg.parts:
            if isinstance(part, (UserPromptPart, TextPart)) and isinstance(
                part.content, str
            ):
                out.append(part.content)
    return out


def test_create_then_get_roundtrips_history_and_state(async_db_url):
    async def body(store):
        session = await store.create(USER)
        session.message_history = [
            _user("2 rooms in Kreuzberg"),
            _assistant("Found 3."),
        ]
        session.state = SessionState(
            total_results=3,
            result_markers=[
                Marker(id="a", lat=52.5012, lng=13.4012, price_warm_eur=1200.0),
                Marker(id="b", lat=52.4999, lng=13.3888, price_warm_eur=None),
            ],
        )
        await store.save(session)
        return session, await store.get(session.id)

    original, got = drive(async_db_url, body)

    assert got.id == original.id
    assert got.user_id == USER
    # History round-trips through JSONB (compare serialized forms — avoids
    # object-identity / timestamp noise).
    assert ModelMessagesTypeAdapter.dump_python(
        got.message_history, mode="json"
    ) == ModelMessagesTypeAdapter.dump_python(original.message_history, mode="json")
    # State round-trips — exercises the columnar marker (de)serializer through JSONB.
    assert got.state.model_dump(mode="json") == original.state.model_dump(mode="json")
    assert got.state.total_results == 3
    assert [m.id for m in got.state.result_markers] == ["a", "b"]


def test_messages_append_across_turns(async_db_url):
    async def body(store):
        session = await store.create(USER)
        session.message_history = [_user("hi"), _assistant("hello")]
        await store.save(session)
        # Turn 2: history grows (the on_complete `all_messages()` semantics).
        session.message_history = session.message_history + [
            _user("more"),
            _assistant("ok"),
        ]
        await store.save(session)
        return await store.get(session.id)

    got = drive(async_db_url, body)
    assert len(got.message_history) == 4
    assert _texts(got.message_history) == ["hi", "hello", "more", "ok"]


def test_history_shrink_triggers_full_rewrite(async_db_url):
    async def body(store):
        session = await store.create(USER)
        session.message_history = [
            _user("a"),
            _assistant("b"),
            _user("c"),
            _assistant("d"),
        ]
        await store.save(session)
        # Diverge: shorter, non-prefix history → full rewrite, not a bad append.
        session.message_history = [_user("new1"), _assistant("new2")]
        await store.save(session)
        return await store.get(session.id)

    got = drive(async_db_url, body)
    assert _texts(got.message_history) == ["new1", "new2"]


def test_get_unknown_or_malformed_id_raises(async_db_url):
    async def body(store):
        with pytest.raises(SessionNotFoundError):
            await store.get(str(uuid.uuid4()))
        with pytest.raises(SessionNotFoundError):
            await store.get("not-a-uuid")

    drive(async_db_url, body)


def test_state_snapshot_overwrites_not_appends(async_db_url):
    async def body(store):
        session = await store.create(USER)
        session.state = SessionState(total_results=1)
        await store.save(session)
        session.state = SessionState(total_results=99)
        await store.save(session)
        return await store.get(session.id)

    got = drive(async_db_url, body)
    assert got.state.total_results == 99
