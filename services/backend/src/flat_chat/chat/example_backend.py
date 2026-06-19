"""⚠️  PLACEHOLDER AGENT — replace this with your own.

This is the reference `AgentBackend` the starter ships with so the app
boots end-to-end out of the box: send a message in the UI and cards +
map markers appear. It is deliberately dumb — NO LLM, NO framework. It
exists to show you the seam, not to be good at finding apartments.

What it does, top to bottom:
  1. Read the latest user message (plain text).
  2. "Parse" it by scanning for a Berlin district name — that's the whole
     NLU. Everything else (price, rooms, amenities, geo-context) is
     ignored. This is the part your agent makes smart.
  3. Run the real `SearchService` with those crude params.
  4. Write the results into `deps.state` so the frontend renders them.
  5. Emit AG-UI events: a `search_apartments` tool-call (lights up the
     status pill), a state snapshot (renders map + cards), and a text
     reply (the chat bubble).

To build your agent: implement `AgentBackend.run` in a new module, then
point `core/dependencies.py:get_chat_service` at it instead of this class.
The two service calls you'll lean on are `deps.search_service.search(...)`
and `deps.listing_service.get(id)`. See `HACKATHON.md`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from ag_ui.core import BaseEvent, RunAgentInput

from flat_chat.chat.backend import (
    latest_user_text,
    state_snapshot,
    text_message,
    tool_call,
)
from flat_chat.chat.state import ChatDeps
from flat_chat.search.schemas import SearchParams

# The 12 Berlin Bezirke + a few well-known Kieze. Matched as case-insensitive
# substrings against the user's message. SearchService does an ILIKE substring
# match on `Listing.district`, so a hit here reliably narrows results.
_BERLIN_DISTRICTS = [
    "Mitte",
    "Friedrichshain",
    "Kreuzberg",
    "Pankow",
    "Prenzlauer Berg",
    "Charlottenburg",
    "Wilmersdorf",
    "Spandau",
    "Steglitz",
    "Zehlendorf",
    "Tempelhof",
    "Schöneberg",
    "Neukölln",
    "Treptow",
    "Köpenick",
    "Marzahn",
    "Hellersdorf",
    "Lichtenberg",
    "Reinickendorf",
    "Wedding",
    "Moabit",
]


def _detect_districts(message: str) -> list[str]:
    """The entire 'natural language understanding' of the example agent."""
    lowered = message.lower()
    return [d for d in _BERLIN_DISTRICTS if d.lower() in lowered]


class ExampleSearchBackend:
    """A no-LLM `AgentBackend` that keyword-matches a district and searches."""

    async def run(
        self, *, run_input: RunAgentInput, deps: ChatDeps
    ) -> AsyncIterator[BaseEvent]:
        message = latest_user_text(run_input)
        districts = _detect_districts(message)
        params = SearchParams(
            districts=districts or None,
            sort_by="recent",  # no embeddings needed; deterministic for a demo
            limit=60,
        )

        results, total = await deps.search_service.search(params)

        # Mutate the shared state. The frontend renders map markers + cards
        # straight from these fields; clearing the active selection drops any
        # detail panel left over from a previous search.
        deps.state.search_params = params
        deps.state.results = results
        deps.state.total_results = total
        deps.state.active_id = None
        deps.state.active_listing_detail = None

        where = f" in {', '.join(districts)}" if districts else ""
        reply = (
            f"Found {total} listing(s){where}, showing {len(results)}. "
            "(This is the placeholder agent — wire up your own for real search.)"
        )

        # Order matters only loosely; the frontend handles events as they
        # arrive. Tool-call → pill, snapshot → map/cards, text → chat bubble.
        for event in tool_call(
            "search_apartments",
            args={"districts": districts},
            result=reply,
        ):
            yield event
        yield state_snapshot(deps)
        for event in text_message(reply):
            yield event
