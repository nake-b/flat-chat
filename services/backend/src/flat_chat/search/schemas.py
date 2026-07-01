"""Search input + output schemas.

`SearchParams` is the tool-arg surface for `search_apartments`. Wide and
flat by design — LLMs handle ~30 flat optional fields better than deep
nesting. Geo-context filters that have internal combinatorial structure
(transit modes ∧ lines ∧ stop name) are allowed one level of nesting.

Bucket labels (NoiseLabel, GreeneryLabel, DensityLabel) live in
`listings.types` — single source of truth across the project.
"""

from datetime import date
from typing import Literal, NamedTuple

from pydantic import BaseModel, Field

from flat_chat.listings.context import ListingCard, Marker
from flat_chat.listings.types import (
    DensityLabel,
    GreeneryLabel,
    NearSpec,
    NoiseLabel,
)

from .geo_filters import (
    HospitalFilter,
    KitaFilter,
    SchoolFilter,
    TransitFilter,
    WaterFilter,
)

SortBy = Literal["relevance", "price", "area", "recent"]

# Server-side caps (NOT LLM-tunable). A search returns EVERY match as a thin
# marker, hard-capped at MARKER_CAP for the SSE snapshot, plus the top
# PREVIEW_N as full cards. Known scale ceiling — grep `MARKER_CAP`.
MARKER_CAP = 5000
PREVIEW_N = 10


class SearchParams(BaseModel):
    """Structured search filters surfaced to the LLM as `search_apartments`
    kwargs. Strictly additive — all fields default to "don't filter".

    Boolean amenity fields use tri-state semantics: `None` means the user
    didn't ask, `True` means must have, `False` means must not have. The
    SearchService translates `True/False` into the corresponding WHERE
    clause; `None` is a no-op.

    Geo-context fields (transit / school / hospital / kita / near_* /
    max_noise / min_greenery / density / inside_ring) are bundled or flat
    depending on whether they take multiple inputs. See
    `agent-compound-docs/decisions/geo-context-thresholds.md` for the
    threshold spec.
    """

    query: str | None = None

    # Money — warm = inkl. Nebenkosten, cold = Kaltmiete.
    price_warm_min: float | None = None
    price_warm_max: float | None = None
    price_cold_max: float | None = None

    # Size
    rooms_min: float | None = None
    rooms_max: float | None = None
    bedrooms_min: int | None = None
    area_sqm_min: float | None = None
    area_sqm_max: float | None = None

    # Location
    districts: list[str] | None = None
    near_lat: float | None = None
    near_lon: float | None = None
    # Opaque `kind:src_id` token from `locate_place` — proximity to ONE named
    # place's exact geometry (line/polygon-precise) via ST_DWithin. Reuses
    # `radius_km`. The backend knows only the token FORMAT, never the tables.
    near_place_ref: str | None = None
    radius_km: float = Field(default=2.0, gt=0, le=50)
    # "Inside the ring" / city-centre — the Umweltzone (S-Bahn ring) flag on
    # gold. True = inside, False = outside; None = don't filter.
    inside_ring: bool | None = None

    # Building / availability
    floor_min: int | None = None
    floor_max: int | None = None
    listing_type: str | None = None
    available_by: date | None = None  # available_from <= available_by

    # Amenities (None = don't filter; True/False = must have / must not have)
    wbs_required: bool | None = None
    is_furnished: bool | None = None
    has_balcony: bool | None = None
    has_kitchen: bool | None = None
    has_elevator: bool | None = None

    has_images: bool | None = None

    # Geo-context filters — see threshold doc for defaults and label vocab.
    # Bundled (multiple inputs each):
    transit: TransitFilter | None = None
    school: SchoolFilter | None = None
    hospital: HospitalFilter | None = None
    kita: KitaFilter | None = None
    near_water: WaterFilter | None = None
    # Flat (single concept each):
    near_park: NearSpec | None = None
    near_playground: NearSpec | None = None
    max_noise: NoiseLabel | None = None
    min_greenery: GreeneryLabel | None = None
    density: DensityLabel | None = None

    sort_by: SortBy = "relevance"
    # No per-search `limit`: the model returns every match as a marker (hard-
    # capped server-side at MARKER_CAP) + a fixed PREVIEW_N of full cards. The
    # LLM doesn't tune result count — "show everything on the map" is the point.


# ---------------------------------------------------------------------------
# Result-set facets — aggregate stats over the WHOLE filtered set.
# ---------------------------------------------------------------------------


class NumericFacet(BaseModel):
    """min / median / max for a numeric column over the full result set.

    Computed in SQL (not from the in-memory markers) so it stays exact even
    when the MARKER_CAP truncation binds, and so it covers columns markers
    don't carry (area). Any field is None when no matched row has a value.
    """

    min: float | None = None
    median: float | None = None
    max: float | None = None


class DistrictCount(BaseModel):
    """One neighbourhood bucket of the result set. `district` holds the Ortsteil
    (ALKIS polygon assignment, e.g. "Prenzlauer Berg") — Berlin's neighbourhood
    granularity, which is how users name areas. Listings without an Ortsteil
    assignment (no pin/polygon) are excluded, so counts can sum to < total."""

    district: str
    count: int


class ResultFacets(BaseModel):
    """Aggregate stats over the entire filtered result set — NOT the preview.

    Surfaced to the agent (via `<result_facets>` in the per-turn prompt) so its
    whole-set summaries ("up to €1,950", "a mix of Prenzlauer Berg and Wedding")
    are grounded in the full set rather than extrapolated from the top-N preview
    cards the LLM can see. Produced by `SearchService._facets`.
    """

    price_warm_eur: NumericFacet | None = None
    area_sqm: NumericFacet | None = None
    districts: list[DistrictCount] = Field(default_factory=list)


class SearchResult(NamedTuple):
    """The full output of `SearchService.search()`.

    A NamedTuple (not a dataclass) so it stays tuple-unpackable — existing
    callers and tests keep `markers, preview, total, facets = await search(...)`
    while gaining named access (`result.facets`). The four tiers:

    - `markers`: EVERY match (≤ MARKER_CAP) as thin tier-1 markers — the map
      source and the ordered result set the LLM indexes into.
    - `preview`: the top PREVIEW_N as full tier-2 cards.
    - `total`: `len(markers)`, unless the cap binds — then a real COUNT(*).
    - `facets`: whole-set aggregate stats (price/area ranges, neighbourhood
      counts), or `None` when total is 0.
    """

    markers: list[Marker]
    preview: list[ListingCard]
    total: int
    facets: ResultFacets | None
