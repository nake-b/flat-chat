"""The SSE stream's last-ditch failure handling.

When an agent run fails mid-stream (e.g. the LLM provider errors after its own
retries are exhausted), the exception must not propagate into Starlette's SSE
writer and silently kill the stream — that's the frozen "thinking" pill. The
`_with_session_and_lock` wrapper catches it and emits a terminal AG-UI
`RUN_ERROR` instead, so the frontend can resolve the pill and offer a retry.

These construct the wrapper directly with a fake stream + lock — no agent, model,
or network.
"""

import asyncio
from contextlib import asynccontextmanager

from ag_ui.core import EventType, RunErrorEvent

import flat_chat.chat.service as service_mod
from flat_chat.chat.service import _with_session_and_lock


@asynccontextmanager
async def _noop_lock():
    yield object()


async def _drain(stream) -> list:
    return [event async for event in stream]


def test_emits_run_error_when_inner_stream_raises():
    async def raising():
        yield "event-1"
        raise RuntimeError("provider blew up")

    out = asyncio.run(_drain(_with_session_and_lock(raising(), "sess-1", _noop_lock())))

    # Events already produced still flow through; a terminal RUN_ERROR is
    # appended in place of the lost completion (not a silent hang).
    assert out[0] == "event-1"
    assert isinstance(out[-1], RunErrorEvent)
    assert out[-1].type == EventType.RUN_ERROR
    assert out[-1].message  # a user-facing message is present


def test_passes_through_cleanly_on_success():
    async def clean():
        yield "a"
        yield "b"

    out = asyncio.run(_drain(_with_session_and_lock(clean(), "sess-2", _noop_lock())))

    # No failure → no synthetic RUN_ERROR appended.
    assert out == ["a", "b"]
    assert not any(isinstance(e, RunErrorEvent) for e in out)


def test_watchdog_emits_run_error_when_stream_stalls(monkeypatch):
    """A stream that goes SILENT (no events, never ends — the stall that the
    per-read timeout can't be trusted to catch) must trip the inactivity watchdog
    and surface a terminal RUN_ERROR instead of hanging forever."""
    monkeypatch.setattr(service_mod, "_SSE_INACTIVITY_TIMEOUT_S", 0.05)

    async def stalling():
        yield "event-1"
        await asyncio.Event().wait()  # hang forever — never yields again
        yield "unreachable"  # pragma: no cover

    out = asyncio.run(
        _drain(_with_session_and_lock(stalling(), "sess-3", _noop_lock()))
    )

    assert out[0] == "event-1"
    assert isinstance(out[-1], RunErrorEvent)
    assert out[-1].type == EventType.RUN_ERROR
    assert out[-1].message
