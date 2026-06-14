"""Pydantic schemas for geo-context filters and per-listing context responses.

Filters extend `SearchParams` (in `schemas.py`) — the LLM sets them through
`search_apartments` tool args. Context shapes are returned by
`GeoContextService.context_for(location)` and surfaced through
`get_listing_details`.

Defaults and label vocab trace to `agent-compound-docs/decisions/
geo-context-thresholds.md`.
"""

from typing import Literal

from pydantic import BaseModel, Field

from .distances import DistanceBucket, NearSpec
from .transit import GtfsMode

# ---------------------------------------------------------------------------
# Filter shapes — surfaced as nested arg objects in `search_apartments`.
# ---------------------------------------------------------------------------


class TransitFilter(BaseModel):
    """Filter listings by proximity to a transit stop.

    Use `modes` to require a specific service type (`["u_bahn"]` = U-Bahn
    nearby; `["u_bahn", "s_bahn"]` = either nearby — OR semantics). Use
    `lines` to require a specific line name (`["U8"]` = a stop served by
    U8). Use `stop_name` to match by name fragment (`"Wittenau"` matches
    "S+U Wittenau"). All three can combine.
    """

    distance: NearSpec = "near"
    modes: list[GtfsMode] | None = None
    lines: list[str] | None = None
    stop_name: str | None = None


class SchoolFilter(BaseModel):
    """Filter listings by proximity to a school.

    `school_type` matches against the Berlin Schulverzeichnis category
    (e.g. "Grundschule", "Gymnasium", "ISS", "Berufsschule"). Left as
    free-text since the source vocabulary is open.
    """

    distance: NearSpec = "near"
    school_type: str | None = None


class HospitalFilter(BaseModel):
    """Filter listings by proximity to a hospital.

    `tier` defaults to `"plan_hospital"` for the filter — when the user
    says "near a hospital" they usually mean emergency-care reachability,
    which is the Krankenhausplan network. The detail-enrichment path uses
    `"any"` so the user sees specialty clinics too.
    """

    distance: NearSpec = "near"
    tier: Literal["plan_hospital", "any"] = "plan_hospital"


# MSS status / dynamics labels — see thresholds doc §8 for the German→English
# mapping. The neutrality requirement is enforced in the agent INSTRUCTIONS.
MssStatus = Literal["disadvantaged", "lower-income", "mixed", "affluent"]
MssDynamics = Literal["slipping", "stable", "improving"]


class MssFilter(BaseModel):
    """Filter listings by neighbourhood socioeconomic character (Sozialmonitoring).

    `status_min` is a *minimum* status floor — `"mixed"` matches mixed
    AND affluent areas. `dynamics` is exact — `"improving"` only matches
    areas trending up faster than Berlin overall.

    These are neighbourhood-character labels, NOT a desirability score.
    A renter seeking "up-and-coming" wants `status_min="disadvantaged"`
    + `dynamics="improving"` (the classic gentrification signature).
    """

    status_min: MssStatus = "lower-income"
    dynamics: MssDynamics | None = None


# ---------------------------------------------------------------------------
# Per-listing context shapes — returned by GeoContextService.context_for().
# ---------------------------------------------------------------------------


class NearestTransitStop(BaseModel):
    stop_id: str
    name: str
    modes: list[GtfsMode]
    lines: list[str]
    distance_m: int
    walk_minutes: int


class NearestSchool(BaseModel):
    name: str | None
    school_type: str | None
    distance_m: int
    operator: str | None = None


class SchoolCatchmentInfo(BaseModel):
    """The primary-school catchment (ESB) the listing falls inside."""

    catchment_id: str | None
    school_number: str | None
    school_name: str | None


class NearestPark(BaseModel):
    name: str | None
    object_type: str | None
    distance_m: int
    area_m2: float | None = None


class NearestPlayground(BaseModel):
    name: str | None
    distance_m: int
    play_area_m2: float | None = None


class NearestHospital(BaseModel):
    name: str | None
    tier: Literal["plan_hospital", "other"]
    distance_m: int
    total_beds: int | None = None


class NearestWater(BaseModel):
    name: str | None
    water_kind: str | None
    distance_m: int


class NoiseProfile(BaseModel):
    label: Literal["quiet", "lively", "noisy"] | None
    total_lden: float | None
    street_lden: float | None
    rail_lden: float | None


class GreeneryProfile(BaseModel):
    """Greenery composite. Cemeteries counted at 0.5 weight (thresholds doc §5)."""

    label: Literal["concrete", "leafy", "very_leafy"] | None
    green_m2_within_300m: float | None


class DensityProfile(BaseModel):
    label: Literal["sparse", "moderate", "dense"] | None
    persons_per_hectare: float | None
    age_under_18_pct: float | None
    age_65_plus_pct: float | None


class MssProfile(BaseModel):
    """Sozialmonitoring profile. Labels are neutral re-codings (thresholds doc §8)."""

    status_label: MssStatus | None
    dynamics_label: MssDynamics | None
    social_inequality_label: str | None
    residents: int | None


class ListingContext(BaseModel):
    """Full per-listing geo-context blob returned by `get_listing_details`."""

    transit: list[NearestTransitStop] = Field(default_factory=list)
    school_catchment: SchoolCatchmentInfo | None = None
    nearest_schools: list[NearestSchool] = Field(default_factory=list)
    nearest_parks: list[NearestPark] = Field(default_factory=list)
    nearest_playground: NearestPlayground | None = None
    nearest_hospitals: list[NearestHospital] = Field(default_factory=list)
    nearest_water: NearestWater | None = None
    noise: NoiseProfile | None = None
    greenery: GreeneryProfile | None = None
    density: DensityProfile | None = None
    mss: MssProfile | None = None
    disabled_parking_count: int = 0


__all__ = [
    # Filter shapes
    "TransitFilter",
    "SchoolFilter",
    "HospitalFilter",
    "MssFilter",
    "MssStatus",
    "MssDynamics",
    # Re-exported type aliases
    "DistanceBucket",
    "NearSpec",
    "GtfsMode",
    # Context shapes
    "ListingContext",
    "NearestTransitStop",
    "NearestSchool",
    "SchoolCatchmentInfo",
    "NearestPark",
    "NearestPlayground",
    "NearestHospital",
    "NearestWater",
    "NoiseProfile",
    "GreeneryProfile",
    "DensityProfile",
    "MssProfile",
]
