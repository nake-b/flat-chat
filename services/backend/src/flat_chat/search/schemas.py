from pydantic import BaseModel


class SearchFilters(BaseModel):
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
    radius_km: float = 2.0
    sort_by: str = "relevance"
    limit: int = 10
