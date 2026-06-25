"""Unit tests for the request-context observability primitives.

Covers the three pure-ish pieces wired in `core/observability.py` and
`core/database.py` that are easy to break in an async refactor:

- `_RequestContextFilter` produces the right `session_prefix` for the
  three combinations (both vars set, only session, neither set).
- `_inject_request_context_comment` prepends `/* session=… run=… */` only
  when at least one var is set, and returns the SQL unchanged otherwise.
- ContextVars don't bleed between concurrent asyncio tasks.

No DB connection required — `_inject_request_context_comment` is a pure
function over its arguments, so we call it directly with sentinel args.
"""

from __future__ import annotations

import asyncio
import logging

from flat_chat.core.database import _inject_request_context_comment
from flat_chat.core.observability import (
    _RequestContextFilter,
    run_id_var,
    session_id_var,
)


def _make_record() -> logging.LogRecord:
    return logging.LogRecord(
        name="flat_chat.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )


def test_request_context_filter_both_set():
    tok_s = session_id_var.set("sid-123")
    tok_r = run_id_var.set("rid-abc")
    try:
        record = _make_record()
        _RequestContextFilter().filter(record)
        assert record.session_prefix == " [session=sid-123 run=rid-abc]"
    finally:
        session_id_var.reset(tok_s)
        run_id_var.reset(tok_r)


def test_request_context_filter_only_session():
    tok = session_id_var.set("sid-only")
    try:
        record = _make_record()
        _RequestContextFilter().filter(record)
        assert record.session_prefix == " [session=sid-only]"
    finally:
        session_id_var.reset(tok)


def test_request_context_filter_neither_set():
    # Both vars are at their default (empty string) — startup / background
    # logs must NOT carry a noisy `[session=]` marker.
    record = _make_record()
    _RequestContextFilter().filter(record)
    assert record.session_prefix == ""


def test_sql_comment_hook_prepends_when_bound():
    tok_s = session_id_var.set("sid-xyz")
    tok_r = run_id_var.set("rid-789")
    try:
        stmt, params = _inject_request_context_comment(
            conn=None,
            cursor=None,
            statement="SELECT 1",
            params={},
            context=None,
            executemany=False,
        )
        assert stmt == "/* session=sid-xyz run=rid-789 */ SELECT 1"
        assert params == {}
    finally:
        session_id_var.reset(tok_s)
        run_id_var.reset(tok_r)


def test_sql_comment_hook_noop_when_unbound():
    # Defaults: both vars empty → statement must round-trip byte-for-byte.
    # This is the property a regression is most likely to break (someone
    # makes the comment unconditional and breaks Alembic / scripts).
    stmt, params = _inject_request_context_comment(
        conn=None,
        cursor=None,
        statement="SELECT 1",
        params={},
        context=None,
        executemany=False,
    )
    assert stmt == "SELECT 1"
    assert params == {}


def test_contextvar_isolation_across_tasks():
    """Two concurrent tasks must not see each other's session ids.

    ContextVars are per-asyncio-task; this is the property the request-
    isolation story depends on. If a future refactor leaks context into a
    shared scope, this test should be the canary.
    """

    async def read_after_set(sid: str) -> str:
        session_id_var.set(sid)
        await asyncio.sleep(0)  # yield so the other task can interleave
        return session_id_var.get()

    async def race() -> tuple[str, str]:
        a, b = await asyncio.gather(
            read_after_set("a-side"), read_after_set("b-side")
        )
        return a, b

    a, b = asyncio.run(race())
    assert a == "a-side"
    assert b == "b-side"
