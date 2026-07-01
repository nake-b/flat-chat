"""Chat-domain pure data containers.

`ChatSession` (one conversation thread) and `ChatDeps` (per-request
bridge between FastAPI and the agent) live here. They hold references
and nothing else — no formatting, no prose composition.

LLM-facing string composition lives in `chat/llm_context.py`. The
SessionState shape (used by both frontend AG-UI mirror and LLM context
prompt) lives in `chat/session_state.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic_ai.messages import ModelMessage

from flat_chat.chat.session_state import SessionState
from flat_chat.users.models import DUMMY_USER_ID

if TYPE_CHECKING:
    from flat_chat.listings.service import ListingService
    from flat_chat.routing.service import RoutingService
    from flat_chat.search.distance import DistanceService
    from flat_chat.search.places import PlaceService
    from flat_chat.search.service import SearchService
    from flat_chat.search.transit_overlays import TransitOverlayService


@dataclass
class ChatSession:
    """One user conversation thread.

    Owns the message history (Pydantic AI's ModelMessage list) and the
    SessionState (canonical in-memory representation of the active
    conversation — used by both the frontend mirror and the LLM context
    builder). Lives in a SessionStore so the storage backend (in-memory
    now, Postgres later) can swap without touching anything else.

    All conversation state — results, params, focus — lives in `state`.
    LLM prose is composed on-demand from `state.result_markers` /
    `state.preview_cards` and `state.active_listing_detail` in
    `chat/llm_context.py`.
    """

    id: str
    user_id: str = DUMMY_USER_ID
    message_history: list[ModelMessage] = field(default_factory=list)
    state: SessionState = field(default_factory=SessionState)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class ChatDeps:
    """Per-request deps handed to the agent and its tools.

    Bridges request-scoped services (search_service, listing_service,
    place_service) with the session-scoped state. Tools mutate `state`
    (in-place); the AG-UI adapter streams JSON Patch deltas of those
    mutations back to the frontend.

    `state` is named to satisfy the `pydantic_ai.ui.StateHandler`
    protocol so the AG-UI adapter can populate it from each incoming
    request envelope and stream deltas of subsequent mutations back.
    """

    search_service: SearchService
    listing_service: ListingService
    place_service: PlaceService
    transit_overlay_service: TransitOverlayService
    routing_service: RoutingService
    distance_service: DistanceService
    session: ChatSession
    # Overwritten per-request by the dispatch path from session.state +
    # the incoming AG-UI envelope's state (frontend-driven changes like
    # `active_id`). The default_factory only matters when running
    # outside the adapter (e.g. unit tests).
    state: SessionState = field(default_factory=SessionState)
