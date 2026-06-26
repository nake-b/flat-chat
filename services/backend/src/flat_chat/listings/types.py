"""Literal-type vocabulary for listing chip labels, transit modes, distance buckets.

These are the canonical strings the Pydantic-AI tool surface, the agent
prose, the frontend UI, and the search filter input all agree on. Single
source of truth — adding a label here is one edit; nothing else needs to
change for the new value to flow through.

Threshold doc: `agent-compound-docs/decisions/geo-context-thresholds.md`.
"""

from typing import Literal

# ---------------------------------------------------------------------------
# Chip-label categoricals — applied by `listings.labels` from raw gold values.
# ---------------------------------------------------------------------------

NoiseLabel = Literal["quiet", "lively", "noisy"]
"""Noise classification — WHO 2018 + EU END Lden thresholds. See doc §3."""

DensityLabel = Literal["sparse", "moderate", "dense"]
"""Population density bucket (persons/hectare). See doc §6."""

GreeneryLabel = Literal["concrete", "leafy", "very_leafy"]
"""Greenery composite (m² parks+playgrounds+0.5·cemeteries / 300m). See doc §4-5."""


# ---------------------------------------------------------------------------
# Distance buckets — user-facing enum for "how close" in filter args. Maps
# to integer meters via `listings.labels.resolve_near_spec()`. See doc §1.
# ---------------------------------------------------------------------------

DistanceBucket = Literal[
    "next_to", "very_near", "near", "walking_distance", "bike_distance"
]
"""Named distance buckets. Mapped to meters in `thresholds.py`."""

NearSpec = DistanceBucket | int
"""Either a named bucket OR a raw int (meters) — lets the LLM choose."""


# ---------------------------------------------------------------------------
# Transit modes — English labels used in tool args / UI. Mapped to the
# integer GTFS Extended Route Type codes that `transit_stops.modes_served`
# stores in Postgres. See doc §7.
# ---------------------------------------------------------------------------

GtfsMode = Literal["mainline", "regional", "s_bahn", "u_bahn", "bus", "tram", "ferry"]
"""Transit service-type enum used by the agent's `transit` filter."""
