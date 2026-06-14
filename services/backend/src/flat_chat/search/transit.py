"""GTFS Extended Route Type ↔ English enum mapping for transit filters.

The `transit_stops.modes_served` column stores integer GTFS Extended codes
from the VBB feed (e.g. 400 = U-Bahn, 109 = S-Bahn). The agent tool surface
uses string enums because LLMs handle "u_bahn" better than 400 for natural-
language → tool-arg mapping. This module bridges the two.

See `agent-compound-docs/decisions/geo-context-thresholds.md` §7 for the
canonical mapping.
"""

from typing import Literal

# User-facing transit-mode enum (tool args, UI labels).
GtfsMode = Literal[
    "mainline", "regional", "s_bahn", "u_bahn", "bus", "tram", "ferry"
]

# Canonical mapping. Codes come from the VBB published GTFS Extended Route Types.
GTFS_MODE_TO_LABEL: dict[int, GtfsMode] = {
    100: "mainline",
    106: "regional",
    109: "s_bahn",
    400: "u_bahn",
    700: "bus",
    900: "tram",
    1000: "ferry",
}

GTFS_LABEL_TO_MODE: dict[GtfsMode, int] = {v: k for k, v in GTFS_MODE_TO_LABEL.items()}

# Human-readable display strings — kept separate from the API enum value so
# the wire-protocol identifier ("u_bahn") and the UI rendering ("U-Bahn")
# can evolve independently.
GTFS_DISPLAY_NAME: dict[GtfsMode, str] = {
    "mainline": "Mainline",
    "regional": "Regional",
    "s_bahn": "S-Bahn",
    "u_bahn": "U-Bahn",
    "bus": "Bus",
    "tram": "Tram",
    "ferry": "Ferry",
}


def resolve_modes(modes: list[GtfsMode]) -> list[int]:
    """Map English mode labels to the GTFS integer codes used in Postgres."""
    return [GTFS_LABEL_TO_MODE[m] for m in modes]


def decode_modes(codes: list[int]) -> list[GtfsMode]:
    """Map GTFS integer codes back to English labels. Unknown codes are dropped."""
    return [GTFS_MODE_TO_LABEL[c] for c in codes if c in GTFS_MODE_TO_LABEL]


def display_modes(codes: list[int]) -> list[str]:
    """Map GTFS integer codes to human-readable display names."""
    return [GTFS_DISPLAY_NAME[m] for m in decode_modes(codes)]
