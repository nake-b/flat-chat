"""The AG-UI event stream hides tool-retry / validation errors from the UI.

`_QuietRetryEventStream._handle_tool_result` (in `chat/service.py`) emits an
EMPTY-content tool result for a `RetryPromptPart` (a failed-validation retry the
agent will redo) so the raw "N validation errors… Fix the errors and try again."
text never reaches the chat, while real `ToolReturnPart` results pass through
untouched.

`_handle_tool_result` is a *private* Pydantic AI method, so this test also acts as
an upgrade tripwire: if a future pydantic-ai reshapes the method or the
`ToolCallResultEvent` schema, these asserts fail loudly. Full rationale:
`agent-compound-docs/decisions/ag-ui-tool-retry-suppression.md`.
"""

from __future__ import annotations

import asyncio

from ag_ui.core import (
    RunAgentInput,
    RunFinishedEvent,
    TextMessageStartEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from pydantic_ai.messages import RetryPromptPart, ToolReturnPart
from pydantic_ai.ui.ag_ui import AGUIEventStream

from flat_chat.chat.service import _FlatChatEventStream


def _stream() -> _FlatChatEventStream:
    run_input = RunAgentInput(
        thread_id="t-1",
        run_id="r-1",
        state={},
        messages=[],
        tools=[],
        context=[],
        forwarded_props={},
    )
    return _FlatChatEventStream(run_input)


async def _collect(stream: _FlatChatEventStream, part) -> list:
    return [event async for event in stream._handle_tool_result(part)]


def test_retry_prompt_yields_empty_content():
    """A RetryPromptPart is emitted as a tool result with empty content."""
    part = RetryPromptPart(
        content="2 validation errors:\n... Fix the errors and try again.",
        tool_name="search_apartments",
        tool_call_id="call-bad",
    )
    events = asyncio.run(_collect(_stream(), part))

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, ToolCallResultEvent)
    assert event.tool_call_id == "call-bad"
    assert event.content == ""  # the error text is suppressed, not forwarded


def test_tool_return_passes_through():
    """A successful ToolReturnPart keeps its real content (e.g. the breadcrumb)."""
    part = ToolReturnPart(
        content="Found 57 apartments in Kreuzberg.",
        tool_name="search_apartments",
        tool_call_id="call-ok",
    )
    events = asyncio.run(_collect(_stream(), part))

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, ToolCallResultEvent)
    assert event.tool_call_id == "call-ok"
    assert event.content == "Found 57 apartments in Kreuzberg."


# ---------------------------------------------------------------------------
# Live search-finish collapse (transform_stream) — issue #22
# ---------------------------------------------------------------------------


def _start(call_id: str, name: str) -> ToolCallStartEvent:
    return ToolCallStartEvent(tool_call_id=call_id, tool_call_name=name)


def _result(call_id: str, content: str) -> ToolCallResultEvent:
    return ToolCallResultEvent(
        message_id=f"m-{call_id}", tool_call_id=call_id, content=content, role="tool"
    )


def _run_finished() -> RunFinishedEvent:
    return RunFinishedEvent(thread_id="t-1", run_id="r-1")


def _run_transform(canned_events: list) -> list:
    """Drive `_FlatChatEventStream.transform_stream` over a canned AG-UI event
    sequence by stubbing the base implementation (which would otherwise consume
    raw Pydantic AI native events). This exercises only our collapse wrapper."""

    async def fake_super(self, stream, on_complete=None):  # noqa: ANN001
        for event in canned_events:
            yield event

    async def go() -> list:
        stream = _stream()
        original = AGUIEventStream.transform_stream
        AGUIEventStream.transform_stream = fake_super  # type: ignore[assignment]
        try:
            return [e async for e in stream.transform_stream(iter([]))]
        finally:
            AGUIEventStream.transform_stream = original  # type: ignore[assignment]

    return asyncio.run(go())


def _net_contents(out: list) -> dict[str, str]:
    """Last result content per tool_call_id (a later empty result blanks a pill).
    This is the net on-screen state after the stream is applied."""
    net: dict[str, str] = {}
    for e in out:
        if isinstance(e, ToolCallResultEvent):
            net[e.tool_call_id] = e.content
    return net


def _order(out: list) -> list:
    """Compact ordered view: ('start'|'result'|'text', id|content-flag)."""
    seq = []
    for e in out:
        if isinstance(e, ToolCallStartEvent):
            seq.append(("start", e.tool_call_id))
        elif isinstance(e, ToolCallResultEvent):
            seq.append(("result", e.tool_call_id, bool(e.content)))
        elif isinstance(e, TextMessageStartEvent):
            seq.append(("text", e.message_id))
    return seq


def test_silent_multi_search_collapses_to_last():
    # Agent searches 0 → broaden → 48 with NO narration between, then answers.
    # Only the last finish survives; the superseded pill is resolved EMPTY before
    # the next "Searching…" (so the two never stack).
    out = _run_transform(
        [
            _start("c1", "search_apartments"),
            _result("c1", "No apartments found matching those criteria."),
            _start("c2", "search_apartments"),
            _result("c2", "Found 48 listings, most recent first."),
            TextMessageStartEvent(message_id="answer"),
            _run_finished(),
        ]
    )
    net = _net_contents(out)
    assert net["c1"] == ""  # superseded → resolved empty
    assert net["c2"].startswith("Found 48 listings")  # last → finish
    # c1's empty result is emitted BEFORE c2's start (no two "Searching" overlap).
    order = _order(out)
    assert order.index(("result", "c1", False)) < order.index(("start", "c2"))


def test_silent_repeated_empty_searches_collapse_to_one():
    # The reported #22 case: three identical "No apartments found" must not stack.
    zero = "No apartments found matching those criteria."
    out = _run_transform(
        [
            _start("c1", "search_apartments"),
            _result("c1", zero),
            _start("c2", "search_apartments"),
            _result("c2", zero),
            _start("c3", "search_apartments"),
            _result("c3", zero),
            TextMessageStartEvent(message_id="answer"),
            _run_finished(),
        ]
    )
    on_screen = {cid: c for cid, c in _net_contents(out).items() if c}
    assert list(on_screen) == ["c3"]


def test_narrated_searches_show_each_result_once():
    # When the agent narrates between searches, each result shows alongside its
    # narration (no two "Searching…" overlap) — the held finish flushes at the
    # narration text, never stacking duplicates.
    out = _run_transform(
        [
            _start("c1", "search_apartments"),
            _result("c1", "Found 3 listings, most recent first."),
            TextMessageStartEvent(message_id="narr-1"),
            _start("c2", "search_apartments"),
            _result("c2", "Found 6 listings, most recent first."),
            TextMessageStartEvent(message_id="narr-2"),
            _start("c3", "search_apartments"),
            _result("c3", "Found 23 listings, most recent first."),
            TextMessageStartEvent(message_id="answer"),
            _run_finished(),
        ]
    )
    net = _net_contents(out)
    # Each search's finish is shown once (interleaved with its narration), and a
    # finish is always emitted before the NEXT search starts — never overlapping.
    assert net["c1"].startswith("Found 3 listings")
    assert net["c2"].startswith("Found 6 listings")
    assert net["c3"].startswith("Found 23 listings")
    order = _order(out)
    assert order.index(("result", "c1", True)) < order.index(("start", "c2"))
    assert order.index(("result", "c2", True)) < order.index(("start", "c3"))


def test_single_search_finish_passes_through():
    out = _run_transform(
        [
            _start("c1", "search_apartments"),
            _result("c1", "Found 12 listings, most recent first."),
            _run_finished(),
        ]
    )
    net = _net_contents(out)
    assert net["c1"].startswith("Found 12 listings")  # not blanked — it's the last


def test_non_search_results_untouched():
    # open_listing finishes are not collapsed.
    out = _run_transform(
        [
            _start("c1", "open_listing"),
            _result("c1", "Opened listing #3"),
            _run_finished(),
        ]
    )
    results = [e for e in out if isinstance(e, ToolCallResultEvent)]
    assert len(results) == 1
    assert results[0].content == "Opened listing #3"
