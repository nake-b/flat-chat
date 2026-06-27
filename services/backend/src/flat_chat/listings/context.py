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
    null-coordinate listings before projecting), price may be null."""

    id: str
    lat: float
    lng: float
    price_warm_eur: float | None = None


# ---------------------------------------------------------------------------
# Map overlay — a geometry the agent draws on the map (the Spree, a U-Bahn
# line, a park/lake polygon, a Bezirk, the inside-the-ring zone). Lives in the
# leaf `listings` layer so both `search/` (the resolvers that build overlays)
# and `chat/` (SessionState + tools) can import it without breaking the
# import-direction rule. The backend sets only SEMANTICS (`kind` / `label` /
# `geojson`); APPEARANCE (colors, opacity, line vs fill) is the frontend's
# job, keyed off `kind` + the geojson geometry type in
# `services/frontend/src/state/overlayStyles.ts`. No `style_hint` — that would
# be a second, drifting source of truth. See agent-compound-docs/decisions/
# map-overlays.md.
# ---------------------------------------------------------------------------

OverlayKind = Literal["place", "transit_line", "bezirk", "ring", "parks"]
OverlayOrigin = Literal["search", "pinned"]

# Geometry simplification applied when resolving an overlay to GeoJSON (shared
# by every resolver — PlaceService, TransitRouteService). Douglas-Peucker
# tolerance in degrees (~0.00005° ≈ 5 m at Berlin's latitude) drops redundant
# vertices on long lines/polygons (the Spree, a Bezirk) while preserving shape;
# `OVERLAY_COORD_DIGITS=5` rounds coordinates to ~1 m. Both keep the GeoJSON
# that rides the AG-UI state snapshot small. Use with
# `ST_SimplifyPreserveTopology` (never breaks rings; a no-op for points).
OVERLAY_SIMPLIFY_TOLERANCE = 0.00005
OVERLAY_COORD_DIGITS = 5

# When a named place is fragmented into many identically-named footprints (e.g.
# a university campus stored as one ALKIS building per row, all named
# "Technische Universität Berlin"), drawing one row looks arbitrary. The overlay
# resolver unions every SAME-kind, SAME-name footprint within this radius of the
# resolved hit into one shape — the *local* cluster only, so a distant same-name
# cluster elsewhere in the city is excluded. Exact-name (not fuzzy) is
# deliberate: within a campus radius sit unrelated neighbours ("UdK Berlin",
# theatres, an embassy) that a similarity floor would wrongly swallow. A
# unique-named place unions to itself (no-op). Metres.
OVERLAY_CLUSTER_RADIUS_M = 500

# A seed alias is a representative POINT ("TU Berlin", "Görli", "Kotti") that
# sits ON its real target. When an overlay resolves to such a point, we snap to
# the nearest footprint (polygon/line, any kind) within this radius and draw
# that — "TU Berlin" → the Hauptgebäude building it marks, "Görli" → the
# Görlitzer Park polygon. The building/park names don't match the alias, so this
# proximity snap (not name matching) is what actually finds the target. Falls
# back to the point itself if nothing solid is within range. Metres.
OVERLAY_SNAP_RADIUS_M = 150


class MapOverlay(BaseModel):
    """One geometry drawn on the map, mirrored to the frontend via SessionState.

    `id` is stable per logical overlay (e.g. `"place:park:42"`,
    `"transit_line:U7"`) so re-drawing replaces rather than duplicates, and the
    frontend can dismiss by id. `origin` drives the clear policy:
      - `"search"` overlays are derived from the active search's spatial anchors
        (`near_place_ref` / `transit.lines`) and are REPLACED on the next search.
      - `"pinned"` overlays come from an explicit `show_on_map` (or proactive
        agent draw) and PERSIST across searches until removed/dismissed.
    `geojson` is a GeoJSON geometry or Feature — source-agnostic (a
    `named_places` shape or a transit route shape look identical here).
    """

    id: str
    kind: OverlayKind
    label: str
    geojson: dict
    origin: OverlayOrigin = "search"


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
