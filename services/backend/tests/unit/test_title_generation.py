"""Unit tests for `chat/title_gen.py` post-processing and first-turn detection.

The LLM call itself is mocked via `_title_agent.override(model=TestModel(...))`
so these tests don't need an API key, network, or DB. They guard the parts of
the title pipeline that are pure-python and would silently corrupt the sidebar
if they regressed: the quote/punctuation stripping, the first-turn predicate,
and the failure-returns-None behaviour.
"""

from __future__ import annotations

import asyncio

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
import pytest
from pydantic_ai.models.test import TestModel

from flat_chat.chat import title_gen
from flat_chat.chat.title_gen import (
    _extract_first_exchange,
    clean_title,
    generate_title,
    is_first_completed_turn,
)


@pytest.fixture
def fake_title_model(monkeypatch):
    """`build_title_model()` requires an API key; tests bypass it via override.

    We need `build_title_model()` to return SOMETHING (anything truthy is fine —
    `_title_agent.override(model=...)` wins over the run-time `model=` arg) so
    `generate_title()` doesn't bail at the build step.
    """
    fake = TestModel(custom_output_text="placeholder")
    monkeypatch.setattr(title_gen, "build_title_model", lambda: fake)
    return fake


def _user(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _assistant(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])


def test_clean_title_strips_surrounding_quotes():
    assert clean_title('"Kreuzberg search"') == "Kreuzberg search"
    assert clean_title("'Kreuzberg search'") == "Kreuzberg search"
    # Smart quotes (the model's most common foot-gun).
    assert clean_title("“Kreuzberg search”") == "Kreuzberg search"


def test_clean_title_strips_trailing_punctuation():
    assert clean_title("Kreuzberg search.") == "Kreuzberg search"
    assert clean_title("Kreuzberg search,") == "Kreuzberg search"
    assert clean_title("Kreuzberg search!") == "Kreuzberg search"


def test_clean_title_drops_title_prefix():
    assert clean_title("Title: Kreuzberg search") == "Kreuzberg search"
    assert clean_title("Titel: Kreuzberg-Suche") == "Kreuzberg-Suche"


def test_clean_title_collapses_multiline_to_first_nonempty_line():
    assert clean_title("\n\nKreuzberg search\n\nlong description") == "Kreuzberg search"


def test_clean_title_truncates_to_60_chars():
    long = "x" * 200
    assert len(clean_title(long) or "") == 60


def test_clean_title_returns_none_for_blank_or_quotes_only():
    assert clean_title("") is None
    assert clean_title("   ") is None
    assert clean_title('""') is None
    assert clean_title(None) is None


def test_is_first_completed_turn_one_user_one_assistant():
    assert is_first_completed_turn([_user("hi"), _assistant("hello")]) is True


def test_is_first_completed_turn_zero_messages_is_false():
    assert is_first_completed_turn([]) is False


def test_is_first_completed_turn_two_user_messages_is_false():
    history: list[ModelMessage] = [
        _user("hi"),
        _assistant("hello"),
        _user("anything else?"),
        _assistant("yes"),
    ]
    assert is_first_completed_turn(history) is False


def test_is_first_completed_turn_ignores_tool_calls_and_retries():
    """Only user prompts + assistant text count toward the turn budget."""
    history: list[ModelMessage] = [
        ModelRequest(parts=[SystemPromptPart(content="be helpful")]),
        _user("hi"),
        ModelResponse(parts=[ToolCallPart(tool_name="search", args={}, tool_call_id="t1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="search", content="ok", tool_call_id="t1")]),
        ModelRequest(parts=[RetryPromptPart(content="try again", tool_call_id="t1")]),
        _assistant("hello"),
    ]
    assert is_first_completed_turn(history) is True


def test_extract_first_exchange_picks_first_of_each():
    history: list[ModelMessage] = [_user("hi"), _assistant("hello"), _user("again")]
    assert _extract_first_exchange(history) == ("hi", "hello")


def test_extract_first_exchange_returns_none_when_incomplete():
    assert _extract_first_exchange([_user("hi")]) is None


def test_generate_title_runs_model_and_cleans_output(fake_title_model):
    history = [_user("Find 2 rooms in Kreuzberg under 1500"), _assistant("Sure.")]
    with title_gen._title_agent.override(
        model=TestModel(custom_output_text='"Kreuzberg 2-room search".')
    ):
        result = asyncio.run(generate_title(history))
    assert result == "Kreuzberg 2-room search"


def test_generate_title_returns_none_on_empty_model_output(fake_title_model):
    history = [_user("Find a flat"), _assistant("Sure.")]
    with title_gen._title_agent.override(model=TestModel(custom_output_text="   ")):
        result = asyncio.run(generate_title(history))
    assert result is None


def test_generate_title_returns_none_when_history_has_no_first_exchange(
    fake_title_model,
):
    """Defensive: a stray call with empty history must not crash."""
    with title_gen._title_agent.override(model=TestModel(custom_output_text="x")):
        result = asyncio.run(generate_title([]))
    assert result is None


def test_generate_title_returns_none_when_model_unavailable(monkeypatch):
    """No API key configured → `build_title_model()` raises → returns None."""

    def boom():
        raise RuntimeError("no provider configured")

    monkeypatch.setattr(title_gen, "build_title_model", boom)
    history = [_user("hi"), _assistant("hello")]
    assert asyncio.run(generate_title(history)) is None
