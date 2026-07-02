"""Agent tool surface — the capabilities the chat Agent is built from.

Each submodule owns one capability plus its co-located tool-protocol prose, so
renaming a tool is a single edit:
  - `core`      — CoreCapability: search / open / page / locate_place.
  - `overlays`  — MapOverlayCapability: draw / hide map geometries.
  - `lenses`    — LensCapability: colour the map by travel time / distance.
  - `proximity` — ListingProximityCapability (DEFERRED): single-listing
    distance / travel-time point queries.
  - `backbone`  — TOOL_BACKBONE, the cross-capability invariants prose.
  - `emission`  — StateEmittingToolset, the forget-proof STATE_SNAPSHOT wrapper.

The internal import direction is acyclic: `core → lenses, overlays, emission`;
`lenses → overlays, emission`; `overlays → emission`; `proximity → emission`;
`backbone`/`emission` import none of the others.

This `__init__` re-exports the public API so callers (agent.py, service.py,
api/chat.py, tests) import from `flat_chat.chat.tools` without caring which
submodule a symbol lives in.
"""

from flat_chat.chat.tools.backbone import TOOL_BACKBONE
from flat_chat.chat.tools.core import (
    SEARCH_TOOL_NAME,
    CoreCapability,
    get_result_page,
    locate_place,
    open_listing,
    search_apartments,
)
from flat_chat.chat.tools.emission import StateEmittingToolset
from flat_chat.chat.tools.lenses import (
    LensCapability,
    apply_distance_lens,
    apply_travel_time_lens,
    clear_lens,
)
from flat_chat.chat.tools.overlays import (
    MapOverlayCapability,
    clear_map_overlays,
    hide_on_map,
    show_on_map,
)
from flat_chat.chat.tools.proximity import (
    ListingProximityCapability,
    distance_to,
    travel_time_to,
)

__all__ = [
    "SEARCH_TOOL_NAME",
    "TOOL_BACKBONE",
    "CoreCapability",
    "MapOverlayCapability",
    "LensCapability",
    "ListingProximityCapability",
    "StateEmittingToolset",
    "search_apartments",
    "open_listing",
    "get_result_page",
    "locate_place",
    "show_on_map",
    "hide_on_map",
    "clear_map_overlays",
    "apply_travel_time_lens",
    "apply_distance_lens",
    "clear_lens",
    "distance_to",
    "travel_time_to",
]
