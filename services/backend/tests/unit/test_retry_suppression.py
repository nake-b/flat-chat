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

from ag_ui.core import RunAgentInput, ToolCallResultEvent
from pydantic_ai.messages import RetryPromptPart, ToolReturnPart

from flat_chat.chat.service import _QuietRetryEventStream


def _stream() -> _QuietRetryEventStream:
    run_input = RunAgentInput(
        thread_id="t-1",
        run_id="r-1",
        state={},
        messages=[],
        tools=[],
        context=[],
        forwarded_props={},
    )
    return _QuietRetryEventStream(run_input)


async def _collect(stream: _QuietRetryEventStream, part) -> list:
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
