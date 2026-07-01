"""Unit tests for `chat/sessions.py:InMemorySessionStore`.

Guards three properties that are easy to break in a refactor:
  - The LRU cap at 100 sessions evicts the oldest entry on overflow.
  - `lock()` never creates state for an unknown session_id — the dict
    would otherwise grow unbounded via the lock() call alone.
  - Lock identity is stable across calls for the same session_id, and
    the lock correctly serialises concurrent users.

`create`/`get`/`save` are async (the Protocol is DB-backed in prod); we drive
them with `asyncio.run`, matching the rest of the suite's convention.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from flat_chat.chat.sessions import InMemorySessionStore, SessionNotFoundError

USER = "00000000-0000-0000-0000-000000000001"


def test_create_returns_retrievable_session_with_uuid_id():
    store = InMemorySessionStore()
    session = asyncio.run(store.create(USER))
    # UUIDs are 36 chars including dashes.
    assert len(session.id) == 36
    assert session.user_id == USER
    assert asyncio.run(store.get(session.id)) is session


def test_get_unknown_id_raises_session_not_found():
    store = InMemorySessionStore()
    with pytest.raises(SessionNotFoundError):
        asyncio.run(store.get("does-not-exist"))


def test_lru_eviction_drops_oldest_when_over_max():
    store = InMemorySessionStore()

    # Fill to capacity. Stamp created_at deterministically so the eviction
    # target is unambiguous — `datetime.now()` granularity could otherwise
    # tie the first two sessions.
    base = datetime(2025, 1, 1, tzinfo=UTC)
    created_ids: list[str] = []
    for i in range(InMemorySessionStore._MAX_SESSIONS):
        s = asyncio.run(store.create(USER))
        s.created_at = base + timedelta(seconds=i)
        created_ids.append(s.id)

    # Acquire a lock on the oldest so we can verify `_locks` is also pruned.
    oldest_id = created_ids[0]
    _ = store.lock(oldest_id)
    assert oldest_id in store._locks

    # One more push it over the edge.
    overflow = asyncio.run(store.create(USER))
    overflow.created_at = base + timedelta(seconds=InMemorySessionStore._MAX_SESSIONS)

    assert overflow.id in store._sessions
    assert oldest_id not in store._sessions
    # Lock map followed the session out.
    assert oldest_id not in store._locks


def test_lock_for_unknown_id_raises_session_not_found():
    # The deliberate "no lock for arbitrary IDs" guard — protects against
    # `_locks` growing unbounded via lock() alone.
    store = InMemorySessionStore()
    with pytest.raises(SessionNotFoundError):
        store.lock("does-not-exist")
    assert store._locks == {}


def test_lock_identity_stable_across_calls():
    store = InMemorySessionStore()
    session = asyncio.run(store.create(USER))
    lock_a = store.lock(session.id)
    lock_b = store.lock(session.id)
    assert lock_a is lock_b


def test_lock_serialises_concurrent_users():
    """Two coroutines both try to acquire the same session lock. The
    second must wait until the first releases."""
    store = InMemorySessionStore()
    session = asyncio.run(store.create(USER))
    order: list[str] = []

    async def hold(label: str, hold_for: float) -> None:
        async with store.lock(session.id):
            order.append(f"{label}-acquired")
            await asyncio.sleep(hold_for)
            order.append(f"{label}-released")

    async def race() -> None:
        # Start A first; give it a head-start so it owns the lock by the
        # time B's `async with` reaches `acquire`.
        a = asyncio.create_task(hold("A", 0.05))
        await asyncio.sleep(0)  # let A enter the critical section
        b = asyncio.create_task(hold("B", 0.0))
        await asyncio.gather(a, b)

    asyncio.run(race())

    # A must fully release before B acquires.
    assert order == ["A-acquired", "A-released", "B-acquired", "B-released"]


def test_save_replaces_session_in_store():
    store = InMemorySessionStore()
    session = asyncio.run(store.create(USER))
    # The contract is "mutations through get() are visible without save()",
    # but the save() hook still installs the passed object — DB-backed
    # impls will rely on this path.
    asyncio.run(store.save(session))
    assert asyncio.run(store.get(session.id)) is session


def test_list_conversation_summaries_filters_empty_and_scopes():
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    store = InMemorySessionStore()
    USER_OTHER = "00000000-0000-0000-0000-000000000002"

    empty = asyncio.run(store.create(USER))  # zero messages → hidden
    with_msg = asyncio.run(store.create(USER))
    with_msg.message_history = [ModelRequest(parts=[UserPromptPart(content="hi")])]
    asyncio.run(store.save(with_msg))

    other = asyncio.run(store.create(USER_OTHER))
    other.message_history = [ModelRequest(parts=[UserPromptPart(content="hey")])]
    asyncio.run(store.save(other))

    rows = asyncio.run(store.list_conversation_summaries(USER))
    ids = [r.id for r in rows]
    assert with_msg.id in ids
    assert empty.id not in ids
    assert other.id not in ids


def test_set_title_if_unset_is_idempotent():
    store = InMemorySessionStore()
    session = asyncio.run(store.create(USER))
    assert asyncio.run(store.set_title_if_unset(session.id, "first")) is True
    assert asyncio.run(store.set_title_if_unset(session.id, "second")) is False
    assert session.title == "first"


def test_set_title_if_unset_unknown_session_returns_false():
    store = InMemorySessionStore()
    assert asyncio.run(store.set_title_if_unset("does-not-exist", "x")) is False


def test_delete_if_owned_happy_path_drops_session_and_lock():
    store = InMemorySessionStore()
    session = asyncio.run(store.create(USER))
    # Force the lock entry to exist so we can verify it's pruned.
    _ = store.lock(session.id)
    assert session.id in store._locks

    assert asyncio.run(store.delete_if_owned(session.id, USER)) is True
    assert session.id not in store._sessions
    assert session.id not in store._locks


def test_delete_if_owned_wrong_user_is_a_noop():
    store = InMemorySessionStore()
    session = asyncio.run(store.create(USER))
    OTHER = "00000000-0000-0000-0000-000000000002"

    assert asyncio.run(store.delete_if_owned(session.id, OTHER)) is False
    # Row stayed put.
    assert asyncio.run(store.get(session.id)) is session


def test_delete_if_owned_unknown_session_returns_false():
    store = InMemorySessionStore()
    assert asyncio.run(store.delete_if_owned("does-not-exist", USER)) is False
