"""Numeric constants for label classification + distance bucket resolution.

Every constant in this module traces to a row in
`agent-compound-docs/decisions/geo-context-thresholds.md`. **Doc-first,
code-second.** Constants without a doc row are technical debt.

Used by:
  - `listings.labels` for the raw-value → label mapping at result time
  - `search.geo_filters` for the label → SQL-threshold mapping at filter time

Both directions of the bucket translation share the same numbers, so a
threshold tweak is one place to edit (here) without rebuilding gold.

This module is duplicated inline in `services/ingestion/src/gold/
enrich_listings.py` (same constants under uppercase names) because the
ingestion service is in a different Python package and intentionally
does NOT import from backend.
"""

from .types import DistanceBucket

# ---------------------------------------------------------------------------
# Noise (Lden, dB). WHO 2018 + EU END. See thresholds doc §3.
# ---------------------------------------------------------------------------

NOISE_QUIET_MAX_LDEN: float = 55.0
NOISE_LIVELY_MAX_LDEN: float = 65.0


# ---------------------------------------------------------------------------
# Population density (persons per hectare). See thresholds doc §6.
# ---------------------------------------------------------------------------

DENSITY_SPARSE_MAX: float = 50.0
DENSITY_MODERATE_MAX: float = 150.0


# ---------------------------------------------------------------------------
# Greenery — m² of (parks + playgrounds + 0.5 * cemeteries) within 300m.
# WHO Europe rule: 0.5 ha = "adequate". See thresholds doc §4.
# ---------------------------------------------------------------------------

GREENERY_LEAFY_MIN_M2: float = 5_000.0
GREENERY_VERY_LEAFY_MIN_M2: float = 10_000.0
GREENERY_BUFFER_M: int = 300


# ---------------------------------------------------------------------------
# Distance ladder for "how close" (meters). Berlin-scaled from canonical
# norms. See thresholds doc §1.
# ---------------------------------------------------------------------------

NEXT_TO_M: int = 150
VERY_NEAR_M: int = 400
NEAR_M: int = 650
WALKING_DIST_M: int = 1200
BIKE_DIST_M: int = 2500

BUCKET_TO_METERS: dict[DistanceBucket, int] = {
    "next_to": NEXT_TO_M,
    "very_near": VERY_NEAR_M,
    "near": NEAR_M,
    "walking_distance": WALKING_DIST_M,
    "bike_distance": BIKE_DIST_M,
}


# ---------------------------------------------------------------------------
# Per-dataset caps for the "k=1 always, k=2..k within cap" rule. Used by
# the gold ETL when assembling the top-K JSONB blobs. See doc §1.
# ---------------------------------------------------------------------------

CAP_SCHOOLS_M: int = 2500
CAP_PARKS_M: int = 1500
CAP_PLAYGROUNDS_M: int = 1000
CAP_HOSPITALS_M: int = 5000
CAP_WATER_M: int = 2000
CAP_TRANSIT_STOPS_M: int = 1500


# ---------------------------------------------------------------------------
# Pedestrian walking speed (m/s). Used by walk-minute conversion.
# Adult average ~5 km/h, also used by EAÖ German transit-planning standards.
# See thresholds doc §2.
# ---------------------------------------------------------------------------

PEDESTRIAN_M_PER_S: float = 1.4


# ---------------------------------------------------------------------------
# Last-mile walk cap (m) for the transit travel-time lens: a listing's transit
# time = min over stops within this range of (anchor→stop + walk(stop→listing)).
# Stops farther than this can't "rescue" an otherwise-unreachable listing.
# 1500 m (~18 min at PEDESTRIAN_M_PER_S) matches CAP_TRANSIT_STOPS_M — Berliners
# routinely walk >1 km to a station. ROUTING-ONLY: not part of the gold ETL, so
# (unlike the caps above) it is NOT duplicated into ingestion. See doc §2.
# ---------------------------------------------------------------------------

CAP_LAST_MILE_WALK_M: int = 1500


# ---------------------------------------------------------------------------
# GTFS Extended Route Type ↔ English mode label. Source: VBB GTFS feed,
# threshold doc §7.
# ---------------------------------------------------------------------------

GTFS_MODE_TO_LABEL: dict[int, str] = {
    100: "mainline",
    106: "regional",
    109: "s_bahn",
    400: "u_bahn",
    700: "bus",
    900: "tram",
    1000: "ferry",
}

GTFS_LABEL_TO_MODE: dict[str, int] = {v: k for k, v in GTFS_MODE_TO_LABEL.items()}

GTFS_DISPLAY_NAME: dict[str, str] = {
    "mainline": "Mainline",
    "regional": "Regional",
    "s_bahn": "S-Bahn",
    "u_bahn": "U-Bahn",
    "bus": "Bus",
    "tram": "Tram",
    "ferry": "Ferry",
}
