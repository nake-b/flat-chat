from typing import Literal

from pydantic import BaseModel, Field

SortBy = Literal["relevance", "price", "area", "recent"]


class SearchParams(BaseModel):
    """Structured search filters surfaced to the LLM as `search_apartments`
    kwargs. Strictly additive — all fields default to "don't filter".

    Boolean amenity fields use tri-state semantics: `None` means the user
    didn't ask, `True` means must have, `False` means must not have. The
    SearchService translates `True/False` into the corresponding WHERE
    clause; `None` is a no-op.
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
    available_by: str | None = None  # ISO date — available_from <= available_by

    # Amenities (None = don't filter; True/False = must have / must not have)
    wbs_required: bool | None = None
    is_furnished: bool | None = None
    has_balcony: bool | None = None
    has_kitchen: bool | None = None
    has_elevator: bool | None = None

    has_images: bool | None = None

    sort_by: SortBy = "relevance"
    limit: int = Field(default=50, ge=1, le=200)
