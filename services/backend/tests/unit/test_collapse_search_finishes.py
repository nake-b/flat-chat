"""Pure-function coverage for the reload-path search-finish collapse.

`_collapse_search_finishes` shapes the transcript that `GET /messages` returns
on reload (issue #22): within a turn, only the LAST `search_apartments` result
keeps its content. The end-to-end contract is pinned by the DB-gated
`test_conversations_api.py`; these tests exercise the collapse in isolation (no
DB) so the branch logic — and its purity — stay locked even when
`TEST_DATABASE_URL` is unset.
"""

from __future__ import annotations

from flat_chat.api.chat import _collapse_search_finishes


def _search_call(call_id: str) -> dict:
    return {
        "role": "assistant",
        "toolCalls": [{"id": call_id, "function": {"name": "search_apartments"}}],
    }


def _tool_result(call_id: str, content: str) -> dict:
    return {"role": "tool", "toolCallId": call_id, "content": content}


def test_collapses_multi_search_turn_to_last_result():
    msgs = [
        {"role": "user", "content": "broaden"},
        _search_call("c1"),
        _tool_result("c1", "No apartments found."),
        _search_call("c2"),
        _tool_result("c2", "Found 48 listings."),
        {"role": "assistant", "content": "Broadened to 48."},
    ]
    out = _collapse_search_finishes(msgs)
    by_id = {m["toolCallId"]: m["content"] for m in out if m.get("role") == "tool"}
    assert by_id["c1"] == ""  # superseded → blanked
    assert by_id["c2"].startswith("Found 48")  # last in turn → kept


def test_last_search_kept_per_turn_across_turns():
    msgs = [
        {"role": "user", "content": "t1"},
        _search_call("c1"),
        _tool_result("c1", "No apartments found."),
        _search_call("c2"),
        _tool_result("c2", "Found 48 listings."),
        {"role": "user", "content": "t2"},
        _search_call("c3"),
        _tool_result("c3", "Found 12 listings."),
    ]
    out = _collapse_search_finishes(msgs)
    by_id = {m["toolCallId"]: m["content"] for m in out if m.get("role") == "tool"}
    assert by_id["c1"] == ""
    assert by_id["c2"].startswith("Found 48")  # last of turn 1
    assert by_id["c3"].startswith("Found 12")  # last (and only) of turn 2


def test_non_search_results_are_untouched():
    msgs = [
        {"role": "user", "content": "open it"},
        {
            "role": "assistant",
            "toolCalls": [{"id": "o1", "function": {"name": "open_listing"}}],
        },
        _tool_result("o1", "Opened listing #3"),
    ]
    out = _collapse_search_finishes(msgs)
    assert out[-1]["content"] == "Opened listing #3"


def test_handles_explicit_null_toolcalls():
    # `dump_messages` emits `toolCalls: null` on non-tool messages — the `or []`
    # guard must not choke on it (a plain `.get(..., [])` default would return
    # None here and raise).
    msgs = [
        {"role": "assistant", "content": "hi", "toolCalls": None},
        {"role": "user", "content": "search"},
        _search_call("c1"),
        _tool_result("c1", "Found 3 listings."),
    ]
    out = _collapse_search_finishes(msgs)
    by_id = {m["toolCallId"]: m["content"] for m in out if m.get("role") == "tool"}
    assert by_id["c1"].startswith("Found 3")


def test_does_not_mutate_input():
    msgs = [
        {"role": "user", "content": "broaden"},
        _search_call("c1"),
        _tool_result("c1", "No apartments found."),
        _search_call("c2"),
        _tool_result("c2", "Found 48 listings."),
    ]
    contents_before = [m.get("content") for m in msgs]
    _collapse_search_finishes(msgs)
    assert [m.get("content") for m in msgs] == contents_before
