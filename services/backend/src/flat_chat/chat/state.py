"""Chat-domain pure data.

`ChatSession` (one conversation thread) and `ChatDeps` (per-request bridge
between FastAPI and the agent) live here. They hold references and nothing
else — no formatting, no prose composition.

LLM-facing string composition lives in `chat/llm_context.py`. Frontend-mirror
types live in `chat/ui_state.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic_ai.messages import ModelMessage

from flat_chat.chat.ui_state import UiState

if TYPE_CHECKING:
    from flat_chat.chat.llm_context import LlmResultSetView
    from flat_chat.search.service import SearchService


@dataclass
class ChatSession:
    """One user conversation thread.

    Owns the message history (Pydantic AI's ModelMessage list), the current
    `LlmResultSetView` under discussion (LLM-facing), and the `UiState`
    (frontend-facing mirror). Lives in a SessionStore so the storage backend
    (in-memory now, Postgres later) can swap without touching anything else.
    """

    id: str
    message_history: list[ModelMessage] = field(default_factory=list)
    result_set: LlmResultSetView | None = None
    ui_state: UiState = field(default_factory=UiState)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class ChatDeps:
    """Per-request deps handed to the agent and its tools.

    Bridges request-scoped services (search_service) with the session-scoped
    state. Tools mutate `session.result_set` (LLM-facing) and `state`
    (frontend-facing) — both persist across messages.

    `state` is named to satisfy the `pydantic_ai.ui.StateHandler` protocol so
    the AG-UI adapter can populate it from each incoming request and stream
    JSON Patch deltas of subsequent mutations back to the frontend.
    """

    search_service: SearchService
    session: ChatSession
    # Overwritten per-request by AGUIAdapter from the AG-UI envelope.
    # The default_factory only matters when running outside the adapter
    # (e.g. unit tests).
    state: UiState = field(default_factory=UiState)
