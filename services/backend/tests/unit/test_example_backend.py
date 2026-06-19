"""Unit tests for the agent seam — `chat/example_backend.py`.

This is the contract every hackathon team builds on, so it's worth a guard:
the placeholder backend must populate `SessionState` from a search and emit
the AG-UI events the frontend needs (a state snapshot, a search tool-call, a
text reply). No DB — `SearchService` is faked so the test stays a pure unit.
"""

from __future__ import annotations

import asyncio

from ag_ui.core import (
    RunAgentInput,
    StateSnapshotEvent,
    TextMessageContentEvent,
    ToolCallStartEvent,
    UserMessage,
)

from flat_chat.chat.example_backend import ExampleSearchBackend, _detect_districts
from flat_chat.chat.session_state import SessionState
from flat_chat.chat.state import ChatDeps, ChatSession


class _FakeSearchService:
    """Records the params it was called with; returns a canned (results, total)."""

    def __init__(self, total: int = 7) -> None:
        self.total = total
        self.last_params = None

    async def search(self, params):
        self.last_params = params
        return [], self.total


def _run_backend(message: str, *, total: int = 7):
    search = _FakeSearchService(total=total)
    deps = ChatDeps(
        search_service=search,  # type: ignore[arg-type]
        listing_service=object(),  # type: ignore[arg-type]  # unused by the example
        session=ChatSession(id="s1"),
        state=SessionState(),
    )
    run_input = RunAgentInput(
        thread_id="t1",
        run_id="r1",
        state={},
        messages=[UserMessage(id="m1", role="user", content=message)],
        tools=[],
        context=[],
        forwarded_props=None,
    )

    async def collect():
        backend = ExampleSearchBackend()
        return [e async for e in backend.run(run_input=run_input, deps=deps)]

    return search, deps, asyncio.run(collect())


def test_detect_districts_matches_case_insensitive_substring():
    assert _detect_districts("cheap flats in KREUZBERG please") == ["Kreuzberg"]
    assert _detect_districts("somewhere in Mitte or Neukölln") == ["Mitte", "Neukölln"]
    assert _detect_districts("anywhere is fine") == []


def test_backend_populates_state_from_search():
    search, deps, _events = _run_backend("2-room flat in Kreuzberg", total=7)
    # The crude parse fed the district into the real SearchParams.
    assert search.last_params.districts == ["Kreuzberg"]
    assert search.last_params.sort_by == "recent"  # no embedder needed
    # State mirrors the search outcome and clears any prior selection.
    assert deps.state.total_results == 7
    assert deps.state.results == []
    assert deps.state.active_id is None
    assert deps.state.search_params is not None


def test_backend_emits_snapshot_toolcall_and_text():
    _search, _deps, events = _run_backend("flats in Kreuzberg", total=7)

    assert any(isinstance(e, StateSnapshotEvent) for e in events)

    tool_starts = [e for e in events if isinstance(e, ToolCallStartEvent)]
    assert [e.tool_call_name for e in tool_starts] == ["search_apartments"]

    text = "".join(
        e.delta for e in events if isinstance(e, TextMessageContentEvent)
    )
    assert "Kreuzberg" in text
    assert "7" in text


def test_backend_handles_no_district():
    search, deps, events = _run_backend("just show me anything", total=3)
    assert search.last_params.districts is None
    assert deps.state.total_results == 3
    assert any(isinstance(e, StateSnapshotEvent) for e in events)
