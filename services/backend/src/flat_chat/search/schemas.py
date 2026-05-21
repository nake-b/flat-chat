from typing import Literal

from pydantic import BaseModel, Field

SortBy = Literal["relevance", "price", "area", "recent"]


class SearchParams(BaseModel):
    query: str | None = None
    price_warm_max: float | None = None
    rooms_min: float | None = None
    rooms_max: float | None = None
    area_sqm_min: float | None = None
    districts: list[str] | None = None
    floor_min: int | None = None
    listing_type: str | None = None
    has_images: bool | None = None
    near_lat: float | None = None
    near_lon: float | None = None
    radius_km: float = Field(default=2.0, gt=0, le=50)
    sort_by: SortBy = "relevance"
    limit: int = Field(default=50, ge=1, le=200)
