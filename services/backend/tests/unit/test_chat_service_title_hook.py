"""Title-generation hook on the first completed agent turn.

`ChatService.on_complete` spawns an `asyncio.create_task(...)` after persistence
that fires the title LLM call, then writes the result via
`SessionStore.set_title_if_unset`. This is the load-bearing regression test for
the whole sidebar feature — a future refactor that moves title generation back
into the persistence path would regress here loudly.

Lives under `tests/unit/` (no `DB_REQUIRED` mark) because everything runs against
the in-memory store + `FunctionModel`/`TestModel` — no Postgres, no API keys.

Strategy: monkeypatch `asyncio.create_task` so the background task is captured
and `await`ed at the end of the test (otherwise the task races the test's exit
and the title hasn't landed when we assert). The control inversion is local
to one fixture so the production path stays bona-fide `create_task`.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from starlette.requests import Request

import flat_chat.chat.service as service_mod
import flat_chat.chat.title_gen as title_gen_mod
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
    return {
        "threadId": thread_id,
        "runId": "run-test",
        "state": {},
        "messages": messages,
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }


class _CaptureTasks:
    """Replace `asyncio.create_task` to collect background tasks for awaiting."""

    def __init__(self) -> None:
        self.tasks: list[asyncio.Task] = []
        self._original = asyncio.create_task

    def __enter__(self) -> _CaptureTasks:
        asyncio.create_task = self._capture  # type: ignore[assignment]
        return self

    def __exit__(self, *exc) -> None:
        asyncio.create_task = self._original  # type: ignore[assignment]

    def _capture(self, coro, **kwargs):  # type: ignore[no-untyped-def]
        task = self._original(coro, **kwargs)
        self.tasks.append(task)
        return task

    async def drain(self) -> None:
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)


async def _run_turn(
    store: InMemorySessionStore,
    session_id: str,
    envelope_messages: list[dict],
    chat_model,
    capture: _CaptureTasks,
) -> None:
    chat = ChatService(
        search_service=None, listing_service=None, place_service=None, store=store
    )
    original_build = service_mod.build_chat_model
    service_mod.build_chat_model = lambda: chat_model
    try:
        resp = await chat.dispatch_agent_request(
            _make_request(_envelope(session_id, envelope_messages)), USER
        )
        async for _ in resp.body_iterator:
            pass
    finally:
        service_mod.build_chat_model = original_build
    await capture.drain()


def _single_text_model(text: str) -> FunctionModel:
    """A FunctionModel that always responds with a single text turn."""

    async def stream_fn(messages, _info: AgentInfo):
        yield text

    return FunctionModel(stream_function=stream_fn)


@pytest.fixture
def fake_title_model(monkeypatch):
    """Pin `build_title_model` to a TestModel so `_title_agent.run` works."""
    monkeypatch.setattr(
        title_gen_mod,
        "build_title_model",
        lambda: TestModel(custom_output_text="Kreuzberg 2-room search"),
    )


def test_title_generated_on_first_completed_turn(fake_title_model):
    async def go():
        store = InMemorySessionStore()
        session = await store.create(USER)
        with _CaptureTasks() as capture:
            await _run_turn(
                store,
                session.id,
                [{"id": "m1", "role": "user", "content": "Find 2 rooms in Kreuzberg"}],
                _single_text_model("Sure."),
                capture,
            )
        return session

    session = asyncio.run(go())
    assert session.title == "Kreuzberg 2-room search"


def test_title_not_regenerated_on_second_turn(fake_title_model, monkeypatch):
    """Second turn must NOT fire the title model again (idempotence)."""
    call_counter = {"n": 0}

    original = title_gen_mod.generate_title

    async def spy(history):
        call_counter["n"] += 1
        return await original(history)

    monkeypatch.setattr(service_mod, "generate_title", spy)

    async def go():
        store = InMemorySessionStore()
        session = await store.create(USER)
        with _CaptureTasks() as capture:
            await _run_turn(
                store,
                session.id,
                [{"id": "m1", "role": "user", "content": "Find a flat"}],
                _single_text_model("Sure."),
                capture,
            )
            await _run_turn(
                store,
                session.id,
                [
                    {"id": "m1", "role": "user", "content": "Find a flat"},
                    {"id": "m2", "role": "assistant", "content": "Sure."},
                    {"id": "m3", "role": "user", "content": "Under 1000?"},
                ],
                _single_text_model("Lots."),
                capture,
            )
        return session, call_counter["n"]

    session, n_calls = asyncio.run(go())
    assert n_calls == 1, f"title generation fired {n_calls}× — expected exactly once"
    assert session.title == "Kreuzberg 2-room search"


def test_title_failure_does_not_break_persistence(monkeypatch):
    """When the title model raises, the conversation persists with title=NULL."""

    def boom():
        raise RuntimeError("provider down")

    # Failure source: provider construction. (Equivalent: a model that raises
    # mid-call; both paths land in the `try/except` inside `generate_title`.)
    monkeypatch.setattr(title_gen_mod, "build_title_model", boom)

    async def go():
        store = InMemorySessionStore()
        session = await store.create(USER)
        with _CaptureTasks() as capture:
            await _run_turn(
                store,
                session.id,
                [{"id": "m1", "role": "user", "content": "hi"}],
                _single_text_model("hello"),
                capture,
            )
        # Persistence still succeeded — history landed.
        return session

    session = asyncio.run(go())
    assert session.title is None
    assert len(session.message_history) >= 2
