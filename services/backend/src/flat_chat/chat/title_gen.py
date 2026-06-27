"""Background conversation-title generation.

A one-shot LLM call summarises the first turn of a conversation into a
sidebar-friendly title (~4 words). Wired into `ChatService.on_complete` so
it fires AFTER persistence has returned — never blocks the user-visible
turn latency, never breaks persistence on failure.

The post-processing + first-turn detection are pure functions so the
test suite can exercise them without standing up a model.
"""

from __future__ import annotations

import logging

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart

from flat_chat.chat.providers import build_title_model

logger = logging.getLogger(__name__)


_TITLE_INSTRUCTIONS = """\
You title chat conversations between a user and a Berlin apartment-search assistant.

Output exactly ONE title, 2 to 5 words. No quotes, no period, no trailing
punctuation, no prefix like "Title:". Capture the apartment-search criteria
the user expressed — neighbourhood, room count, budget, special constraint —
NOT greetings or filler.

Match the language of the user's message (German if German, English if English).
If the user only said hello or asked a generic question with no criteria yet,
output "Neue Suche" if the user wrote in German, else "New search".
"""


# Module-level agent. `output_type=str` returns the raw model text — no
# Pydantic validation wrapper, which would otherwise re-prompt on punctuation
# the model picked up despite the instructions.
_title_agent: Agent[None, str] = Agent(
    deps_type=None,
    output_type=str,
    instructions=_TITLE_INSTRUCTIONS,
    retries={"tools": 0, "output": 0},
)


_TITLE_MAX_CHARS = 60


def is_first_completed_turn(history: list[ModelMessage]) -> bool:
    """True iff `history` represents exactly one user→assistant exchange.

    Uses the same projection rule as `_serialize_history` in `api/chat.py`:
    `UserPromptPart` counts as a user message, `TextPart` on a `ModelResponse`
    counts as an assistant message. Tool calls, system prompts, retries and
    thinking parts don't count toward the turn budget.
    """
    user_count = 0
    assistant_count = 0
    for msg in history:
        if isinstance(msg, ModelRequest):
            user_count += sum(
                1 for part in msg.parts if isinstance(part, UserPromptPart)
            )
        elif isinstance(msg, ModelResponse):
            assistant_count += sum(
                1 for part in msg.parts if isinstance(part, TextPart)
            )
    return user_count == 1 and assistant_count == 1


def _extract_first_exchange(history: list[ModelMessage]) -> tuple[str, str] | None:
    """Pull (first user prompt text, first assistant text) out of the history."""
    user_text: str | None = None
    assistant_text: str | None = None
    for msg in history:
        if isinstance(msg, ModelRequest) and user_text is None:
            for part in msg.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    user_text = part.content
                    break
        elif isinstance(msg, ModelResponse) and assistant_text is None:
            for part in msg.parts:
                if isinstance(part, TextPart) and isinstance(part.content, str):
                    assistant_text = part.content
                    break
        if user_text is not None and assistant_text is not None:
            return user_text, assistant_text
    return None


def clean_title(raw: str | None) -> str | None:
    """Normalise model output into a sidebar-safe title, or None to skip persist.

    Strips surrounding whitespace + quotes (`"`, `'`, `“`, `”`, `‘`, `’`,
    `«`, `»`), strips a trailing period or comma, collapses multi-line
    output to the first non-empty line, and truncates to 60 chars.
    Returns None when nothing usable remains.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    # Collapse multi-line: a chatty model with a heading and a body, etc.
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            text = stripped
            break
    # Drop any "Title:" / "Titel:" prefix the model may have emitted.
    for prefix in ("Title:", "title:", "Titel:", "titel:"):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
    # Strip trailing punctuation BEFORE quote-stripping — a `"Title".` ends in
    # `.` not `"`, so the quote pass below wouldn't fire without this.
    text = text.rstrip(".,;:!?").strip()
    # Strip surrounding quotes / brackets, then re-trim any newly-exposed punct.
    quote_chars = "\"'“”‘’«»"
    while len(text) >= 2 and text[0] in quote_chars and text[-1] in quote_chars:
        text = text[1:-1].strip().rstrip(".,;:!?").strip()
    if not text:
        return None
    return text[:_TITLE_MAX_CHARS]


async def generate_title(history: list[ModelMessage]) -> str | None:
    """Run the one-shot title LLM call against the first user/assistant pair.

    Returns the cleaned title, or None if the call errored, the history had
    no usable first exchange, or the cleaned output was empty. Callers MUST
    decide what to do with None (typically: leave the DB title NULL).
    """
    exchange = _extract_first_exchange(history)
    if exchange is None:
        return None
    user_text, assistant_text = exchange
    prompt = f"USER:\n{user_text}\n\nASSISTANT:\n{assistant_text}"
    try:
        model = build_title_model()
    except RuntimeError as exc:
        logger.warning("Title model unavailable: %s", exc)
        return None
    try:
        result = await _title_agent.run(prompt, model=model)
    except Exception:
        logger.exception("Title generation call failed")
        return None
    return clean_title(result.output)
