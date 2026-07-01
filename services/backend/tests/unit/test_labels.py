"""Unit tests for `listings/labels.py`.

Bucket boundaries and threshold-table round-trips. The constants in
`listings/thresholds.py` are co-owned by search filtering and result-time
labels — if anyone tweaks them and the bucket math drifts, these tests
catch it before search and labels disagree on what "quiet" means.

Pure functions, no DB.
"""

from __future__ import annotations

import pytest

from flat_chat.listings.labels import (
    bucket_density,
    bucket_greenery,
    bucket_noise,
    decode_modes,
    display_modes,
    encode_modes,
    primary_transit_line,
    resolve_near_spec,
    transit_mode,
    walk_minutes,
)
from flat_chat.listings.thresholds import (
    BUCKET_TO_METERS,
    DENSITY_MODERATE_MAX,
    DENSITY_SPARSE_MAX,
    GREENERY_LEAFY_MIN_M2,
    GREENERY_VERY_LEAFY_MIN_M2,
    GTFS_LABEL_TO_MODE,
    NOISE_LIVELY_MAX_LDEN,
    NOISE_QUIET_MAX_LDEN,
)

# ---------------------------------------------------------------------------
# bucket_noise — `< quiet_max` quiet, `< lively_max` lively, else noisy
# ---------------------------------------------------------------------------


def test_bucket_noise_none_returns_none():
    assert bucket_noise(None) is None


@pytest.mark.parametrize(
    "lden, expected",
    [
        (0.0, "quiet"),
        (NOISE_QUIET_MAX_LDEN - 0.001, "quiet"),
        (NOISE_QUIET_MAX_LDEN, "lively"),  # boundary: 55.0 is NOT quiet
        (NOISE_LIVELY_MAX_LDEN - 0.001, "lively"),
        (NOISE_LIVELY_MAX_LDEN, "noisy"),  # boundary: 65.0 is noisy
        (100.0, "noisy"),
    ],
)
def test_bucket_noise_boundaries(lden, expected):
    assert bucket_noise(lden) == expected


# ---------------------------------------------------------------------------
# bucket_density
# ---------------------------------------------------------------------------


def test_bucket_density_none_returns_none():
    assert bucket_density(None) is None


@pytest.mark.parametrize(
    "pph, expected",
    [
        (0.0, "sparse"),
        (DENSITY_SPARSE_MAX - 0.001, "sparse"),
        (DENSITY_SPARSE_MAX, "moderate"),
        (DENSITY_MODERATE_MAX - 0.001, "moderate"),
        (DENSITY_MODERATE_MAX, "dense"),
        (1000.0, "dense"),
    ],
)
def test_bucket_density_boundaries(pph, expected):
    assert bucket_density(pph) == expected


# ---------------------------------------------------------------------------
# bucket_greenery — `>= very_leafy_min` very_leafy, `>= leafy_min` leafy, else concrete
# ---------------------------------------------------------------------------


def test_bucket_greenery_none_returns_none():
    assert bucket_greenery(None) is None


@pytest.mark.parametrize(
    "m2, expected",
    [
        (0.0, "concrete"),
        (GREENERY_LEAFY_MIN_M2 - 0.001, "concrete"),
        (GREENERY_LEAFY_MIN_M2, "leafy"),  # boundary
        (GREENERY_VERY_LEAFY_MIN_M2 - 0.001, "leafy"),
        (GREENERY_VERY_LEAFY_MIN_M2, "very_leafy"),  # boundary
        (50_000.0, "very_leafy"),
    ],
)
def test_bucket_greenery_boundaries(m2, expected):
    assert bucket_greenery(m2) == expected


# ---------------------------------------------------------------------------
# walk_minutes — 1.4 m/s, integer minutes, min-1 floor for positive distance
# ---------------------------------------------------------------------------


def test_walk_minutes_none_returns_none():
    assert walk_minutes(None) is None


def test_walk_minutes_negative_returns_none():
    assert walk_minutes(-1) is None


def test_walk_minutes_zero_returns_zero():
    assert walk_minutes(0) == 0


def test_walk_minutes_subminute_rounds_up_to_one():
    # 50m / 1.4 m/s ≈ 35.7s → 0.6min → round(0.6)=1 anyway, but `max(_, 1)`
    # is the guard for the case where round() lands at 0. Cover that here.
    assert walk_minutes(50) == 1
    # 10m would round to 0; the floor must lift it to 1.
    assert walk_minutes(10) == 1


def test_walk_minutes_round_to_nearest():
    # 1000m / 1.4 m/s = 714s = 11.9min → round = 12.
    assert walk_minutes(1000) == 12


# ---------------------------------------------------------------------------
# resolve_near_spec — int passthrough, label → meters via BUCKET_TO_METERS
# ---------------------------------------------------------------------------


def test_resolve_near_spec_int_passthrough():
    assert resolve_near_spec(123) == 123


@pytest.mark.parametrize("label", list(BUCKET_TO_METERS.keys()))
def test_resolve_near_spec_label(label):
    assert resolve_near_spec(label) == BUCKET_TO_METERS[label]


# ---------------------------------------------------------------------------
# encode_modes / decode_modes — round-trip GTFS labels through int codes
# ---------------------------------------------------------------------------


def test_encode_decode_round_trip():
    modes = ["u_bahn", "s_bahn", "tram"]
    assert decode_modes(encode_modes(modes)) == modes


def test_decode_modes_drops_unknown_codes():
    # 999 is not a known GTFS code — it must be silently dropped, not raise.
    decoded = decode_modes([400, 999, 700])
    assert decoded == ["u_bahn", "bus"]


def test_encode_modes_uses_threshold_mapping():
    # Sanity-check the source-of-truth wiring: encoded codes must match
    # GTFS_LABEL_TO_MODE entry by entry.
    for label, code in GTFS_LABEL_TO_MODE.items():
        assert encode_modes([label]) == [code]


def test_display_modes_returns_human_readable_names():
    # 400 = u_bahn → "U-Bahn"; 109 = s_bahn → "S-Bahn".
    assert display_modes([400, 109]) == ["U-Bahn", "S-Bahn"]


def test_transit_mode_classifies_by_prefix():
    assert transit_mode("U7") == "u_bahn"
    assert transit_mode("S41") == "s_bahn"
    assert transit_mode("M10") == "tram"
    assert transit_mode("N7") == "night"
    assert transit_mode("245") == "bus"
    assert transit_mode("X9") == "bus"


def test_primary_transit_line_prefers_rail_over_bus():
    # A stop serving buses + a U-Bahn should surface the U-Bahn, not the
    # array's first element.
    assert primary_transit_line(["140", "248", "N7", "U7"]) == "U7"
    # Rail beats tram.
    assert primary_transit_line(["M10", "S1"]) == "S1"
    # Single bus line stays as-is (this is the confusing "245" case).
    assert primary_transit_line(["245"]) == "245"
    # Night bus is last resort.
    assert primary_transit_line(["N1", "N7"]) == "N1"


def test_primary_transit_line_none_on_empty():
    assert primary_transit_line(None) is None
    assert primary_transit_line([]) is None
