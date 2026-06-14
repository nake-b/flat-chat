from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from .buckets import DensityLabel, GreeneryLabel, NoiseLabel
from .geo_filters import (
    HospitalFilter,
    MssFilter,
    NearSpec,
    SchoolFilter,
    TransitFilter,
)

SortBy = Literal["relevance", "price", "area", "recent"]


class SearchParams(BaseModel):
    """Structured search filters surfaced to the LLM as `search_apartments`
    kwargs. Strictly additive — all fields default to "don't filter".

    Boolean amenity fields use tri-state semantics: `None` means the user
    didn't ask, `True` means must have, `False` means must not have. The
    SearchService translates `True/False` into the corresponding WHERE
    clause; `None` is a no-op.

    Geo-context fields (transit / school / hospital / mss / near_* / max_noise
    / min_greenery / density) are bundled or flat depending on whether they
    take multiple inputs. See `agent-compound-docs/decisions/
    geo-context-thresholds.md` for the threshold spec.
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
    radius_km: float = Field(default=2.0, gt=0, le=50)

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
    mss: MssFilter | None = None
    # Flat (single concept each):
    near_park: NearSpec | None = None
    near_playground: NearSpec | None = None
    near_water: NearSpec | None = None
    max_noise: NoiseLabel | None = None
    min_greenery: GreeneryLabel | None = None
    density: DensityLabel | None = None

    sort_by: SortBy = "relevance"
    limit: int = Field(default=50, ge=1, le=200)
