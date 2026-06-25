"""SessionState — canonical in-memory representation of the active conversation.

One per conversation thread. Lives in `ChatSession.state` and gets mirrored
to the frontend over the AG-UI stream as JSON-Patch deltas. The same object
serves three readers:
  - The LLM (via `chat/llm_context.py:build_dynamic_state_prompt` — emits
    `<current_state>` + `<user_focus>` XML for each turn)
  - The frontend (renders markers/cards/detail panel from these fields)
  - The agent's pagination tool `get_result_page` (zero-DB-hit re-read of
    the current result set)

Fields are intentionally co-located: the applied search params (the
question), the results (the answer), and the active selection (the
focus) are one object. No separate `LlmResultSetView.params` /
`UiState.results` / `UiState.active_id` split — that's what we had before
the refactor and it spread one conversation's "current situation" across
three locations.

Naming note: industry convention (AWS Bedrock, LangGraph, etc.) calls
this `SessionState`. The AG-UI protocol uses `STATE_SNAPSHOT` as the
event type that puts this on the wire — "snapshot" is the wire moment,
"state" is the persistent thing in memory.

Architecture-decision doc: `agent-compound-docs/decisions/session-state-design.md`
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from flat_chat.listings.context import ListingDetail, ListingCard
from flat_chat.search.schemas import SearchParams


class SessionState(BaseModel):
    """Shared state mirrored between backend (truth) and frontend (read).

    AG-UI streams JSON Patch deltas of this object to the frontend on
    every mutation by an agent tool. The frontend's CopilotKit store
    applies the patches; `useSessionState()` exposes the result.
    Write-back: when the user clicks a card, the frontend sets
    `active_id` (and HTTP-fetches the detail, then writes that into
    `active_listing_detail`) so the agent's next turn already has both.
    """

    # The applied search (the question)
    search_params: SearchParams | None = None
    """The filters the LLM used for the active result set. Co-located with
    results so prose like "you searched for 2-room flats in Kreuzberg, found
    487, here are 5" comes from one place."""

    total_results: int = 0
    """How many listings matched the active search in total (≥ len(results))."""

    # The materialized result set (the answer)
    results: list[ListingCard] = Field(default_factory=list)
    """Apartments currently displayed on the map and in the card strip.
    Tier-2 each (~500 B); 500 results ≈ 250 KB — fine for SSE state."""

    # The active interaction (the focus)
    active_id: str | None = None
    """The id of the card currently expanded into detail view, if any."""

    active_listing_detail: ListingDetail | None = None
    """Full tier-3 detail for `active_id`. Populated whenever active_id is set
    (either by the agent's open_listing tool or by the frontend's HTTP
    fetch on card click). Cleared on next search. The agent reads this in
    the `<user_focus>` block so it can answer follow-up questions about
    the open listing without a tool call."""
