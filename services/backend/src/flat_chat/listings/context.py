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

from typing import Literal

from pydantic import BaseModel, Field

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
    the warm rent (the `price_warm` lens); after `apply_travel_time` it is
    the commute time in minutes (the `commute_min` lens). The map colours
    pins by this value against a per-lens ramp owned by the frontend
    (`state/lensStyles.ts`); identity lives once in `marker_lens`, never
    repeated per marker. May be null (e.g. a listing with no price, or
    unreachable in the active travel lens) → rendered in a neutral "no data"
    colour."""

    id: str
    lat: float
    lng: float
    lens_value: float | None = None


class MarkerLens(BaseModel):
    """Names the single scalar every `Marker.lens_value` currently carries —
    the active map visualization lens. Lives once in `SessionState`, not per
    marker. The backend sets SEMANTICS (`key` + human `label`); the frontend
    owns APPEARANCE (colour ramp / domain / number format) keyed off `key` in
    `state/lensStyles.ts` — same semantics/appearance split as `MapOverlay`.

    Default `price_warm` → the frontend renders the plain pin (today's look, no
    heatmap); `commute_min` → a travel-time ramp. Adding a future lens (e.g.
    a noise heatmap) is one registry entry + the backend populating that scalar,
    nothing structural."""

    key: str = "price_warm"
    label: str | None = None


class TravelTimeFilter(BaseModel):
    """The active commute lens — a resolved anchor + travel mode + optional
    cutoff. Carried in `SessionState` so the shared marker derivation can
    re-apply it after a follow-up `search_apartments` (which rebuilds markers
    from SQL and would otherwise drop the lens). `RoutingService.resolve`
    consumes it to compute per-listing travel time.

    `anchor_label` is the human name ("TU Berlin") used for the lens label
    and any isochrone overlay; `anchor_lat`/`anchor_lng` are the resolved
    coordinates. `max_minutes` set → hard filter (drop listings over the limit);
    None → annotate + colour only (no filtering).

    `schedule_as_of` / `schedule_stale` describe the TRANSIT timetable the
    result was computed against. MOTIS loads a finite VBB feed window; when it
    has lapsed the routing layer clamps the departure to the last covered day
    and sets `schedule_stale=True` + `schedule_as_of=<that date>` so the UI /
    agent can say "schedule as of <date>". Both stay defaulted for car mode
    (driving is date-independent) and for an in-window transit feed."""

    anchor_label: str
    anchor_lat: float
    anchor_lng: float
    mode: Literal["transit", "car"]
    max_minutes: int | None = None
    schedule_as_of: str | None = None
    schedule_stale: bool = False


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
