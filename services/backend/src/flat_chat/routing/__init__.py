"""Routing domain — travel-time computation against external engines.

Sibling of `search/` and `listings/`. Agent-only (like `SearchService`): the
LLM owns interpretation; this layer just turns an anchor + the active result
set into per-listing travel minutes. Two engines, picked by `mode`:

  - **car** → OSRM `/table` (one anchor → many listings in one matrix call)
  - **transit** → MOTIS `one-to-many` (street walk legs + timetable)

Both engines are internal Docker services (`OSRM_URL` / `MOTIS_URL`). Routing
is a *fallible external* dependency — failures surface to the agent as a tool
error so it can degrade gracefully ("couldn't compute travel times — proceed
without?"), unlike the always-available SQL filters in `search/`.

See `agent-compound-docs/decisions/travel-time-routing.md`.
"""

from flat_chat.routing.errors import RoutingError
from flat_chat.routing.service import RoutingService

__all__ = ["RoutingError", "RoutingService"]
