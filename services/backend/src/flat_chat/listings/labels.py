"""Raw-value → label functions for listing chips.

Pure presentation/interpretation layer:
  - `bucket_noise(lden) → NoiseLabel | None`
  - `bucket_density(persons_per_ha) → DensityLabel | None`
  - `bucket_greenery(green_m2_within_300m) → GreeneryLabel | None`
  - `walk_minutes(distance_m) → int`
  - `resolve_near_spec(spec) → int` — bucket label / int meters → meters
  - `encode_modes(modes)` / `decode_modes(codes)` — GTFS mode ↔ label

Threshold tables live in `thresholds.py`; this module is just the mapping
functions on top. Threshold tweaks happen there and both filter parsing
(`search.geo_filters`) and result-time label application read the same
numbers.

Gold stores facts; this module (consumed at chat-presentation time) tells
the user what they mean.
"""

from typing import cast

from .thresholds import (
    BUCKET_TO_METERS,
    DENSITY_MODERATE_MAX,
    DENSITY_SPARSE_MAX,
    GREENERY_LEAFY_MIN_M2,
    GREENERY_VERY_LEAFY_MIN_M2,
    GTFS_DISPLAY_NAME,
    GTFS_LABEL_TO_MODE,
    GTFS_MODE_TO_LABEL,
    NOISE_LIVELY_MAX_LDEN,
    NOISE_QUIET_MAX_LDEN,
    PEDESTRIAN_M_PER_S,
)
from .types import DensityLabel, GreeneryLabel, GtfsMode, NearSpec, NoiseLabel


def bucket_noise(total_lden: float | None) -> NoiseLabel | None:
    """Classify a Lden value into a 3-bucket label. Returns None on None input."""
    if total_lden is None:
        return None
    if total_lden < NOISE_QUIET_MAX_LDEN:
        return "quiet"
    if total_lden < NOISE_LIVELY_MAX_LDEN:
        return "lively"
    return "noisy"


def bucket_density(persons_per_ha: float | None) -> DensityLabel | None:
    """Classify a population density into a 3-bucket label."""
    if persons_per_ha is None:
        return None
    if persons_per_ha < DENSITY_SPARSE_MAX:
        return "sparse"
    if persons_per_ha < DENSITY_MODERATE_MAX:
        return "moderate"
    return "dense"


def bucket_greenery(green_m2_within_300m: float | None) -> GreeneryLabel | None:
    """Classify a green-area-within-300m total into a 3-bucket label.

    Caller is responsible for the cemetery 0.5-weight rule (the gold ETL
    applies this before storing the value).
    """
    if green_m2_within_300m is None:
        return None
    if green_m2_within_300m >= GREENERY_VERY_LEAFY_MIN_M2:
        return "very_leafy"
    if green_m2_within_300m >= GREENERY_LEAFY_MIN_M2:
        return "leafy"
    return "concrete"


def walk_minutes(meters: int | None) -> int | None:
    """Convert meters to integer walking minutes at 1.4 m/s (~5 km/h)."""
    if meters is None or meters < 0:
        return None
    if meters == 0:
        return 0
    minutes = round(meters / PEDESTRIAN_M_PER_S / 60)
    return max(minutes, 1)


def resolve_near_spec(spec: NearSpec) -> int:
    """Resolve a `NearSpec` (bucket label or raw meters) to integer meters."""
    if isinstance(spec, int):
        return spec
    return BUCKET_TO_METERS[spec]


# GTFS mode helpers — bridge the int codes stored in
# `transit_stops.modes_served` to the English labels used everywhere else.


def encode_modes(modes: list[GtfsMode]) -> list[int]:
    """Map English mode labels to GTFS integer codes used in Postgres."""
    return [GTFS_LABEL_TO_MODE[m] for m in modes]


def decode_modes(codes: list[int]) -> list[GtfsMode]:
    """Map GTFS integer codes back to English labels. Unknown codes dropped."""
    # `GTFS_MODE_TO_LABEL` values are GtfsMode literals, but typed `dict[int,
    # str]`, so the comprehension is `list[str]` — cast back. (Annotating the
    # dict as `dict[int, GtfsMode]` instead made ty's inference diverge by
    # platform: macOS honoured it, Linux CI didn't.)
    labels = [GTFS_MODE_TO_LABEL[c] for c in codes if c in GTFS_MODE_TO_LABEL]
    return cast("list[GtfsMode]", labels)


def display_modes(codes: list[int]) -> list[str]:
    """Map GTFS integer codes to human-readable display names."""
    return [GTFS_DISPLAY_NAME[m] for m in decode_modes(codes)]
