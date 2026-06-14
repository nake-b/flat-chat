"""Distance buckets and per-dataset caps for geo-context filters.

Every constant in this module traces to a row in
`agent-compound-docs/decisions/geo-context-thresholds.md`. Read that doc
before changing any value — it captures the source authority (CNU, EU END,
WHO, DWDS, Calthorpe TOD), the original research value, and the
Berlin-delta rationale.

Rule: doc-first, code-second. Constants without a row in the threshold doc
are technical debt.
"""

from typing import Literal

# User-facing distance enum. Maps to meters via `resolve_near_spec()`.
DistanceBucket = Literal[
    "next_to", "very_near", "near", "walking_distance", "bike_distance"
]

# A near-spec is either a named bucket or a raw meter override — lets the LLM
# pick the bucket for "near a park" while still allowing "within 750m" precise.
NearSpec = DistanceBucket | int

# Walking-distance ladder (meters). Berlin-scaled from canonical norms —
# see thresholds doc §1 for source values and rationale per bucket.
NEXT_TO_M: int = 150
VERY_NEAR_M: int = 400
NEAR_M: int = 650  # default for "near X" filters
WALKING_DIST_M: int = 1200
BIKE_DIST_M: int = 2500

_BUCKET_TO_METERS: dict[DistanceBucket, int] = {
    "next_to": NEXT_TO_M,
    "very_near": VERY_NEAR_M,
    "near": NEAR_M,
    "walking_distance": WALKING_DIST_M,
    "bike_distance": BIKE_DIST_M,
}

# Per-dataset caps for the "k=1 always returns, k=2..k only within cap" rule
# used inside GeoContextService._nearest_*. See thresholds doc §1.
CAP_SCHOOLS_M: int = 2500
CAP_PARKS_M: int = 1500
CAP_PLAYGROUNDS_M: int = 1000
CAP_HOSPITALS_M: int = 5000
CAP_WATER_M: int = 2000
CAP_TRANSIT_STOPS_M: int = 1500

# Greenery filter radii (WHO Europe rule, threshold doc §4). Used in the
# cheap proxy filter inside `GeoContextService._apply_greenery_filter`.
GREENERY_LEAFY_RADIUS_M: int = 300
GREENERY_VERY_LEAFY_RADIUS_M: int = 150

# Pedestrian walking speed for walk-minute conversion (UI chips).
# Adult average ~5 km/h, also used by EAÖ German transit-planning standards.
# See thresholds doc §2.
_PEDESTRIAN_M_PER_S: float = 1.4


def resolve_near_spec(spec: NearSpec) -> int:
    """Resolve a `NearSpec` (bucket label or raw meters) to integer meters."""
    if isinstance(spec, int):
        return spec
    return _BUCKET_TO_METERS[spec]


def walk_minutes(meters: int) -> int:
    """Convert distance in meters to integer minutes at 1.4 m/s.

    Rounds to the nearest minute with a minimum of 1 (a chip reading "0 min"
    is uninformative — if you're within walking-trivial distance, "1 min"
    reads more naturally).
    """
    if meters <= 0:
        return 0
    minutes = round(meters / _PEDESTRIAN_M_PER_S / 60)
    return max(minutes, 1)
