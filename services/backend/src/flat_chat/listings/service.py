"""ListingService — unified async accessor for listing data.

Three callers, one accessor:
  - `chat/tools.py:open_listing` — single-index detail fetch for the LLM
  - `api/listings.py:get_listing` — HTTP `GET /api/listings/{id}` for the
    frontend's detail panel
  - Future `BookmarkService.list` — batch-hydrate tier-2 cards for a
    user's bookmarked IDs (no active search snapshot to read from)

Reads `listings ⨝ listings_geo_context ⨝ listings_embeddings` (the join
that delivers everything in one round-trip). The JSONB blobs in
`listings_geo_context` are parsed directly into the `ListingContext`
Pydantic models — gold and the model shapes agree by construction (gold
writes `jsonb_build_object(...)` with the same keys the model expects).

This service does NOT filter or rank — that's `SearchService`'s job.
Here we're just looking up specific listings by ID.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .context import (
    DensityProfile,
    GreeneryProfile,
    ListingDetail,
    MssProfile,
    NearestHospital,
    NearestPark,
    NearestPlayground,
    NearestSchool,
    NearestTransitStop,
    NearestWater,
    NoiseProfile,
    SchoolCatchmentInfo,
)
from .labels import (
    bucket_density,
    bucket_greenery,
    bucket_noise,
    decode_modes,
    walk_minutes,
)
from .models import Listing, ListingGeoContext


class ListingService:
    """Direct reads of listing data — agent-callable AND HTTP-callable."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, listing_id: str | uuid.UUID) -> ListingDetail | None:
        """Fetch one listing's full tier-3 detail by ID.

        Returns None if no listing matches — the HTTP route surfaces this
        as a 404; the agent tool returns a "not found" message to the LLM.
        Accepts the id as a string (the form `UiApartment.id` carries) or
        UUID; coerces internally before the lookup.
        """
        try:
            uid = uuid.UUID(str(listing_id))
        except ValueError:
            return None

        stmt = (
            select(Listing, ListingGeoContext)
            .outerjoin(
                ListingGeoContext, ListingGeoContext.listing_id == Listing.id
            )
            .where(Listing.id == uid)
        )
        result = await self.db.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return None
        listing, lgc = row
        return self._project(listing, lgc)

    # ---- Internal: ORM rows → ListingDetail Pydantic ----

    @staticmethod
    def _project(
        listing: Listing, lgc: ListingGeoContext | None
    ) -> ListingDetail:
        """Build a ListingDetail from a listing + (optional) gold row."""
        # Tier-2 listing fields. Available even when gold isn't populated.
        detail = ListingDetail(
            id=str(listing.id),
            title=listing.title,
            description=listing.description,
            address=listing.address,
            district=listing.district,
            postal_code=listing.postal_code,
            latitude=listing.latitude,
            longitude=listing.longitude,
            price_warm_eur=listing.warm_rent_eur,
            price_cold_eur=listing.cold_rent_eur,
            nebenkosten_eur=listing.nebenkosten_eur,
            kaution_eur=listing.kaution_eur,
            rooms=listing.rooms,
            bedrooms=listing.bedrooms,
            bathrooms=listing.bathrooms,
            area_sqm=listing.area_sqm,
            floor=listing.floor,
            floors_total=listing.floors_total,
            construction_year=listing.construction_year,
            available_from=(
                listing.available_from.isoformat()
                if listing.available_from
                else None
            ),
            listing_type=listing.apartment_type,
            heating=listing.heating,
            energy_consumption_kwh=listing.energy_consumption_kwh,
            wbs_required=listing.wbs_required,
            is_furnished=listing.is_furnished,
            has_kitchen=listing.has_kitchen,
            has_balcony=listing.has_balcony,
            has_elevator=listing.has_elevator,
            has_garden=listing.has_garden,
            features=listing.features,
            images=_image_urls(listing.images),
            lister_type=listing.lister_type,
            source_url=listing.listing_url,
        )

        if lgc is None:
            return detail

        # Tier-3 geo-context. Each JSONB blob is parsed into its model;
        # bucket labels are layered on at construction time so consumers
        # see fresh labels even if thresholds changed since the gold rebuild.
        detail.nearest_transit_stops = _parse_transit_top3(lgc.transit_top3)
        detail.school_catchment = (
            SchoolCatchmentInfo(**lgc.school_catchment)
            if lgc.school_catchment
            else None
        )
        detail.nearest_schools = (
            [NearestSchool(**s) for s in (lgc.schools_top3 or [])]
        )
        detail.nearest_parks = (
            [NearestPark(**p) for p in (lgc.parks_top2 or [])]
        )
        detail.nearest_playground = (
            NearestPlayground(**lgc.playground) if lgc.playground else None
        )
        detail.nearest_hospitals = (
            [NearestHospital(**h) for h in (lgc.hospitals_top2 or [])]
        )
        detail.nearest_water = (
            NearestWater(**lgc.water) if lgc.water else None
        )
        detail.noise = _build_noise_profile(lgc.noise_profile)
        detail.greenery = _build_greenery_profile(lgc.greenery_profile)
        detail.density = _build_density_profile(lgc.density_profile)
        detail.mss = MssProfile(**lgc.mss_profile) if lgc.mss_profile else None
        detail.disabled_parking_count = lgc.disabled_parking_count or 0
        return detail


# ---------------------------------------------------------------------------
# JSONB → Pydantic helpers
# ---------------------------------------------------------------------------


def _image_urls(images: list | None) -> list[str]:
    """Pull a flat list of URLs out of the JSONB `images` column.

    Source schema varies — some are plain URL strings, some are
    `{"url": "..."}` objects. Flatten to plain URLs; drop anything
    unrecognised. Empty input becomes an empty list, not None.
    """
    if not images:
        return []
    out: list[str] = []
    for item in images:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict) and isinstance(item.get("url"), str):
            out.append(item["url"])
    return out


def _parse_transit_top3(blob: list | None) -> list[NearestTransitStop]:
    """Parse the gold-stored transit_top3 JSONB into typed models.

    `modes` arrives as int codes (GTFS Extended); decode to English labels.
    `walk_minutes` is computed from `distance_m` at parse time.
    """
    if not blob:
        return []
    out: list[NearestTransitStop] = []
    for item in blob:
        modes = decode_modes(list(item.get("modes", [])))
        out.append(
            NearestTransitStop(
                stop_id=str(item.get("stop_id", "")),
                name=item.get("name", ""),
                modes=modes,
                lines=list(item.get("lines", [])),
                distance_m=int(item.get("distance_m", 0)),
                walk_minutes=walk_minutes(int(item.get("distance_m", 0))),
            )
        )
    return out


def _build_noise_profile(blob: dict | None) -> NoiseProfile | None:
    if not blob:
        return None
    total = blob.get("total_lden")
    return NoiseProfile(
        label=bucket_noise(total),
        total_lden=total,
        street_lden=blob.get("street_lden"),
        rail_lden=blob.get("rail_lden"),
        distance_m=blob.get("distance_m"),
    )


def _build_greenery_profile(blob: dict | None) -> GreeneryProfile | None:
    if not blob:
        return None
    green_m2 = blob.get("green_m2_within_300m")
    return GreeneryProfile(
        label=bucket_greenery(green_m2),
        green_m2_within_300m=green_m2,
    )


def _build_density_profile(blob: dict | None) -> DensityProfile | None:
    if not blob:
        return None
    pph = blob.get("persons_per_hectare")
    return DensityProfile(
        label=bucket_density(pph),
        persons_per_hectare=pph,
        population=blob.get("population"),
        age_under_6=blob.get("age_under_6"),
        age_6_to_10=blob.get("age_6_to_10"),
        age_10_to_18=blob.get("age_10_to_18"),
        age_18_to_65=blob.get("age_18_to_65"),
        age_65_to_70=blob.get("age_65_to_70"),
        age_70_to_75=blob.get("age_70_to_75"),
        age_75_to_80=blob.get("age_75_to_80"),
        age_80_plus=blob.get("age_80_plus"),
    )
