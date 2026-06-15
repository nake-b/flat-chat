"""Bucket classifiers for noise, density, and greenery.

All thresholds are absolute — drawn from WHO 2018, EU END, WHO Europe, and
general urban-planning literature, not from Berlin-local quantiles. A quiet
street is < 55 dB Lden everywhere in the world, not relative to local norms.

Every constant traces to a row in
`agent-compound-docs/decisions/geo-context-thresholds.md`. Doc-first,
code-second.
"""

from typing import Literal

NoiseLabel = Literal["quiet", "lively", "noisy"]
DensityLabel = Literal["sparse", "moderate", "dense"]
GreeneryLabel = Literal["concrete", "leafy", "very_leafy"]

# Noise (Lden, dB). WHO 2018 + EU END. See thresholds doc §3.
NOISE_QUIET_MAX_LDEN: float = 55.0
NOISE_LIVELY_MAX_LDEN: float = 65.0  # >= 65 = noisy ("high exposure" per EU END)

# Population density (persons per hectare). See thresholds doc §6.
DENSITY_SPARSE_MAX: float = 50.0
DENSITY_MODERATE_MAX: float = 150.0

# Greenery — m² of (parks + playgrounds + 0.5 * cemeteries) within 300m of
# the listing. WHO Europe rule: 0.5 ha = "adequate". See thresholds doc §4.
GREENERY_LEAFY_MIN_M2: float = 5_000.0  # 0.5 hectare
GREENERY_VERY_LEAFY_MIN_M2: float = 10_000.0  # 1.0 hectare


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

    Caller is responsible for applying the cemetery 0.5-weight rule before
    passing the value in — see thresholds doc §5.
    """
    if green_m2_within_300m is None:
        return None
    if green_m2_within_300m >= GREENERY_VERY_LEAFY_MIN_M2:
        return "very_leafy"
    if green_m2_within_300m >= GREENERY_LEAFY_MIN_M2:
        return "leafy"
    return "concrete"
