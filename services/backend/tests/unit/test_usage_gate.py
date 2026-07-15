"""The §3 per-user budget gate in `ChatService.dispatch_agent_request`.

A caller who has spent their rolling-24h budget is rejected BEFORE any model is
built or run — a clean `QuotaExceededError` (→ 429 at the route), zero tokens
spent. Complements the integration test of the ledger arithmetic
(`test_usage_ledger.py`); here we assert the control-flow: gate fires, run never
starts. Under budget → the run proceeds normally (and the token cap is applied,
but that's exercised via the real adapter, not asserted here).

Drives the real dispatch path with an `InMemorySessionStore` (no DB) and a
recording `FunctionModel`, mirroring `test_dispatch_history.py`.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic_ai.models.function import AgentInfo, FunctionModel
from starlette.requests import Request

import flat_chat.chat.service as service_mod
from flat_chat.chat.service import ChatService
from flat_chat.chat.sessions import InMemorySessionStore
from flat_chat.chat.usage import QuotaExceededError

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


def _envelope(thread_id: str) -> dict:
    return {
        "threadId": thread_id,
        "runId": "run-test",
        "state": {},
        "messages": [{"id": "m1", "role": "user", "content": "hi"}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }


class _StubUsage:
    """Fake UsageService: reports a fixed spend and records nothing."""

    def __init__(self, spent: int) -> None:
        self._spent = spent
        self.recorded: list = []

    async def spent_last_24h(self, user_id: str) -> int:
        return self._spent

    async def record(self, user_id, usage, conversation_id=None) -> None:
        self.recorded.append((user_id, usage))


async def _dispatch(spent: int):
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
        usage_service=_StubUsage(spent),
    )

    built = {"called": False}

    async def stream_fn(messages, _info: AgentInfo):
        yield "done"

    def _build():
        built["called"] = True
        return FunctionModel(stream_function=stream_fn)

    original = service_mod.build_chat_model
    service_mod.build_chat_model = _build
    try:
        req = _make_request(_envelope(session.id))
        resp = await chat.dispatch_agent_request(req, USER)
        async for _ in resp.body_iterator:
            pass
    finally:
        service_mod.build_chat_model = original
    return built["called"]


def test_over_budget_rejected_before_run(monkeypatch):
    """Spend at/over the budget → QuotaExceededError, model never built."""
    monkeypatch.setattr(service_mod.settings, "llm_daily_token_budget", 100_000)

    async def body():
        with pytest.raises(QuotaExceededError):
            await _dispatch(spent=100_000)

    asyncio.run(body())


def test_under_budget_proceeds(monkeypatch):
    """Comfortably under budget → the run proceeds (model built, no raise)."""
    monkeypatch.setattr(service_mod.settings, "llm_daily_token_budget", 100_000)
    built = asyncio.run(_dispatch(spent=0))
    assert built is True


def test_budget_zero_disables_gate(monkeypatch):
    """budget == 0 → gate skipped even if reported spend is huge."""
    monkeypatch.setattr(service_mod.settings, "llm_daily_token_budget", 0)
    built = asyncio.run(_dispatch(spent=10**9))
    assert built is True
