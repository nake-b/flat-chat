"""Tier-3 detail Pydantic models — the shape of `active_listing_detail`.

These models are returned by `ListingService.get(id)` and stored in
`SessionState.active_listing_detail`. The frontend reads them via the
HTTP `GET /api/listings/{id}` response and (for agent-driven detail
opens) via the AG-UI state stream.

The shapes mirror the JSONB blob columns on `listings_geo_context`
exactly — gold writes a `jsonb_build_object(...)` per blob, and
`ListingService` parses each blob into the corresponding model. Adding a
new field is: column in the gold UPDATE → field here → frontend render.

Labels (NoiseLabel, DensityLabel, etc.) are populated at construction
time from `listings.labels` — gold stores raw numbers only, so consumers
get a fresh label even if thresholds changed since the gold rebuild.
"""

from __future__ import annotations

from typing import Literal, NamedTuple

from pydantic import BaseModel, Field

# Re-exported so existing `from flat_chat.listings.context import MarkerLens`
# call sites keep working after the lens vocab moved to the `lenses` leaf.
from .lenses import MarkerLens as MarkerLens
from .types import (
    DensityLabel,
    GreeneryLabel,
    GtfsMode,
    NoiseLabel,
)

# ---------------------------------------------------------------------------
# Top-K nearest dataclasses
# ---------------------------------------------------------------------------


class NearestTransitStop(BaseModel):
    stop_id: str
    name: str
    modes: list[GtfsMode]
    lines: list[str]
    distance_m: int
    walk_minutes: int | None = None


class NearestSchool(BaseModel):
    name: str | None = None
    school_type: str | None = None
    distance_m: int


class SchoolCatchmentInfo(BaseModel):
    """The primary-school catchment (Einschulungsbereich) the listing is inside."""

    catchment_id: str | None = None
    school_number: str | None = None
    school_name: str | None = None


class NearestPark(BaseModel):
    name: str | None = None
    distance_m: int


class NearestPlayground(BaseModel):
    name: str | None = None
    distance_m: int


class NearestHospital(BaseModel):
    name: str | None = None
    tier: Literal["plan_hospital", "other"] | None = None
    distance_m: int


class NearestWater(BaseModel):
    name: str | None = None
    water_kind: str | None = None
    distance_m: int


class NearestKita(BaseModel):
    name: str | None = None
    distance_m: int


class NearestLandmark(BaseModel):
    name: str | None = None
    category: str | None = None
    distance_m: int


# ---------------------------------------------------------------------------
# Profile composites — raw values + bucket label co-located.
# ---------------------------------------------------------------------------


class NoiseProfile(BaseModel):
    label: NoiseLabel | None = None
    total_lden: float | None = None
    total_lnight: float | None = None
    street_lden: float | None = None
    rail_lden: float | None = None
    distance_m: int | None = None


class GreeneryProfile(BaseModel):
    """Greenery composite — cemeteries counted at 0.5 weight (gold ETL applies)."""

    label: GreeneryLabel | None = None
    green_m2_within_300m: float | None = None


class DensityProfile(BaseModel):
    label: DensityLabel | None = None
    persons_per_hectare: float | None = None
    population: int | None = None
    age_under_6: int | None = None
    age_6_to_10: int | None = None
    age_10_to_18: int | None = None
    age_18_to_65: int | None = None
    age_65_to_70: int | None = None
    age_70_to_75: int | None = None
    age_75_to_80: int | None = None
    age_80_plus: int | None = None


# ---------------------------------------------------------------------------
# Tier-1 marker — the thinnest projection: id + position + price. EVERY match
# of a search ships as one of these (≤ MARKER_CAP) so the map can plot the
# whole result set. SessionState compacts a list of these to a columnar dict
# on the wire. The ordered list of markers IS the result set: the 1-based
# indices the LLM/user reference resolve against it.
# ---------------------------------------------------------------------------


class Marker(BaseModel):
    """One map marker — tier-1. lat/lng are required (search drops
    null-coordinate listings before projecting).

    `lens_value` is the ONE active visualization scalar for this marker —
    whatever `SessionState.marker_lens` currently names. By default that is
    the warm rent (the `price_warm` lens); under a travel lens it is the
    commute time in minutes (`commute_min`), under a distance lens the
    straight-line distance in metres (`distance_m`). The map colours pins by
    this value against a per-lens ramp owned by the frontend
    (`state/lensStyles.ts`); identity lives once in `marker_lens`, never
    repeated per marker. May be null (e.g. a listing with no price, or
    unreachable under the active lens) → rendered in a neutral "no data"
    colour."""

    id: str
    lat: float
    lng: float
    lens_value: float | None = None


class Anchor(NamedTuple):
    """A place resolved to a routing/distance anchor — the human name plus a
    single point (a geometry centroid). Returned by `PlaceService.anchor_point`
    and fed to the OSRM/MOTIS engines. NamedTuple so it stays unpackable at
    existing `label, lat, lon = anchor` call sites."""

    label: str
    lat: float
    lon: float


# ---------------------------------------------------------------------------
# Tier-2 card shape — the top-N `preview_cards` kept hot in `SessionState`
# and the shape `GET /api/listings?ids=…&view=card` returns for lazy
# hydration. Labels are populated at projection time from `listings.labels`
# (raw values from gold get bucketed). The three tiers (tier-1 markers,
# tier-2 cards = ListingCard, tier-3 detail = ListingDetail) are documented
# in `agent-compound-docs/decisions/agent-vs-http-data-flow.md`.
# ---------------------------------------------------------------------------


class ListingCard(BaseModel):
    """One listing as the frontend renders it on the map and in cards.

    Returned as `SearchService.search()`'s preview slice and by the
    `?view=card` batch route; mirrored to the frontend as
    `SessionState.preview_cards[]` over the AG-UI stream. Labels are derived
    from raw gold values via `listings.labels` at projection time, so
    threshold tweaks don't require a gold rebuild.
    """

    id: str
    lat: float | None = None
    lng: float | None = None

    # Money — full breakdown for the detail panel
    price_warm_eur: float | None = None
    price_cold_eur: float | None = None
    nebenkosten_eur: float | None = None
    kaution_eur: float | None = None

    # Size
    rooms: float | None = None
    bedrooms: int | None = None
    area_sqm: float | None = None

    # Building / availability
    floor: int | None = None
    floors_total: int | None = None
    available_from: str | None = None  # ISO date string
    listing_type: str | None = None

    # Location
    district: str | None = None
    title: str | None = None
    address: str | None = None

    # Amenities (most-asked subset surfaced as chips; rest in detail panel)
    wbs_required: bool | None = None
    is_furnished: bool | None = None
    has_balcony: bool | None = None
    has_kitchen: bool | None = None
    has_elevator: bool | None = None
    has_garden: bool | None = None

    # Energy
    heating: str | None = None
    energy_consumption_kwh: float | None = None

    # Source
    lister_type: str | None = None
    source_url: str | None = None
    image_url: str | None = None  # first image, for marker/card thumbnail

    # Chips — derived from gold's raw values + labels from `listings.labels`
    nearest_transit_line: str | None = None
    walk_min_to_transit: int | None = None
    nearest_park_name: str | None = None
    nearest_park_m: int | None = None
    noise_label: NoiseLabel | None = None
    density_label: DensityLabel | None = None
    # Admin-area context — cheap scalars off `listings_geo_context`, surfaced
    # for the card's location chips ("inside the ring", Bezirk/Ortsteil).
    inside_ring: bool | None = None
    listing_bezirk: str | None = None
    listing_ortsteil: str | None = None

    # Semantic-search score (cosine similarity, when query was set)
    similarity_score: float | None = None


# ---------------------------------------------------------------------------
# The complete listing detail blob — what `GET /api/listings/{id}` returns
# and what lives in `SessionState.active_listing_detail`.
# ---------------------------------------------------------------------------


class ListingDetail(BaseModel):
    """Full tier-3 detail for one listing — listing fields + geo-context."""

    # Listing identity + raw fields (from `listings` table)
    id: str
    title: str | None = None
    description: str | None = None
    address: str | None = None
    district: str | None = None
    postal_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    # Admin-area context (ALKIS polygon assignment + Umweltzone ring flag)
    inside_ring: bool | None = None
    listing_bezirk: str | None = None
    listing_ortsteil: str | None = None

    # Money
    price_warm_eur: float | None = None
    price_cold_eur: float | None = None
    nebenkosten_eur: float | None = None
    kaution_eur: float | None = None

    # Size
    rooms: float | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    area_sqm: float | None = None

    # Building / availability
    floor: int | None = None
    floors_total: int | None = None
    construction_year: int | None = None
    available_from: str | None = None
    listing_type: str | None = None

    # Energy
    heating: str | None = None
    energy_consumption_kwh: float | None = None

    # Amenities
    wbs_required: bool | None = None
    is_furnished: bool | None = None
    has_kitchen: bool | None = None
    has_balcony: bool | None = None
    has_elevator: bool | None = None
    has_garden: bool | None = None

    # Free-form
    features: list[str] | None = None
    images: list[str] = Field(default_factory=list)

    # Listing source signal
    lister_type: str | None = None
    source_url: str | None = None

    # Geo-context tier-3 (from gold JSONB blobs)
    nearest_transit_stops: list[NearestTransitStop] = Field(default_factory=list)
    school_catchment: SchoolCatchmentInfo | None = None
    nearest_schools: list[NearestSchool] = Field(default_factory=list)
    nearest_parks: list[NearestPark] = Field(default_factory=list)
    nearest_playground: NearestPlayground | None = None
    nearest_hospitals: list[NearestHospital] = Field(default_factory=list)
    nearest_water: NearestWater | None = None
    nearest_kitas: list[NearestKita] = Field(default_factory=list)
    nearest_landmarks: list[NearestLandmark] = Field(default_factory=list)
    noise: NoiseProfile | None = None
    greenery: GreeneryProfile | None = None
    density: DensityProfile | None = None
    disabled_parking_count: int = 0
