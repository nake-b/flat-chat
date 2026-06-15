"""ListingService — unified async accessor for listing data.

Three callers, one accessor:
  - `chat/tools.py:open_listing` — single-index detail fetch for the LLM
  - `api/listings.py:get_listing` — HTTP `GET /api/listings/{id}` for the
    frontend's detail panel
  - Future `BookmarkService.list` — batch-hydrate tier-2 cards for a
    user's bookmarked IDs (no active search snapshot to read from)

Data sources:
  - `listings ⨝ listings_geo_context` for the listing row + scalar /
    field geo-context (noise, greenery, density, MSS, school catchment,
    disabled parking).
  - `listings_nearby_*` junction tables for POI sets (top-N by rank for
    transit / schools / hospitals / parks / playgrounds / water).

This service does NOT filter or rank — that's `SearchService`'s job.
Here we're just looking up specific listings by ID. See
`agent-compound-docs/decisions/spatial-neighbor-tables.md` for the
junction-table rationale.
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
from .models import (
    Listing,
    ListingGeoContext,
    ListingNearbyHospital,
    ListingNearbyPark,
    ListingNearbyPlayground,
    ListingNearbySchool,
    ListingNearbyTransit,
    ListingNearbyWater,
)


# Top-N caps for the detail panel — match the v1 (pre-junction) shape so
# the frontend sees the same number of rows per family.
_TRANSIT_TOP_N = 3
_SCHOOLS_TOP_N = 3
_PARKS_TOP_N = 2
_PLAYGROUNDS_TOP_N = 1
_HOSPITALS_TOP_N = 2
_WATER_TOP_N = 1


class ListingService:
    """Direct reads of listing data — agent-callable AND HTTP-callable."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, listing_id: str | uuid.UUID) -> ListingDetail | None:
        """Fetch one listing's full tier-3 detail by ID.

        Returns None if no listing matches — the HTTP route surfaces this
        as a 404; the agent tool returns a "not found" message to the LLM.
        Accepts the id as a string or UUID.

        Reads in two passes: listing + gold scalar/field row, then top-N
        per junction-table family. Six small indexed lookups
        (`(listing_id, rank)`) — ~30 ms total versus the old 12-query
        sequential JSONB fan-out.
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

        detail = self._project_listing(listing, lgc)
        if lgc is None:
            return detail

        # Fetch top-N from each junction table — small lookups against the
        # `(listing_id, distance_m)` B-tree index.
        detail.nearest_transit_stops = await self._fetch_transit(uid)
        detail.nearest_schools = await self._fetch_schools(uid)
        detail.nearest_parks = await self._fetch_parks(uid)
        detail.nearest_playground = await self._fetch_playground(uid)
        detail.nearest_hospitals = await self._fetch_hospitals(uid)
        detail.nearest_water = await self._fetch_water(uid)
        return detail

    # ---- Junction-table fetches (top-N by rank) ----

    async def _fetch_transit(self, listing_id: uuid.UUID) -> list[NearestTransitStop]:
        nbr = ListingNearbyTransit
        stmt = (
            select(nbr.stop_id, nbr.name, nbr.modes, nbr.lines, nbr.distance_m)
            .where(nbr.listing_id == listing_id)
            .order_by(nbr.rank)
            .limit(_TRANSIT_TOP_N)
        )
        rows = (await self.db.execute(stmt)).all()
        return [
            NearestTransitStop(
                stop_id=row.stop_id,
                name=row.name or "",
                modes=decode_modes(list(row.modes or [])),
                lines=list(row.lines or []),
                distance_m=row.distance_m,
                walk_minutes=walk_minutes(row.distance_m),
            )
            for row in rows
        ]

    async def _fetch_schools(self, listing_id: uuid.UUID) -> list[NearestSchool]:
        nbr = ListingNearbySchool
        stmt = (
            select(nbr.name, nbr.school_type, nbr.distance_m)
            .where(nbr.listing_id == listing_id)
            .order_by(nbr.rank)
            .limit(_SCHOOLS_TOP_N)
        )
        rows = (await self.db.execute(stmt)).all()
        return [
            NearestSchool(
                name=row.name or "",
                school_type=row.school_type,
                distance_m=row.distance_m,
            )
            for row in rows
        ]

    async def _fetch_parks(self, listing_id: uuid.UUID) -> list[NearestPark]:
        nbr = ListingNearbyPark
        stmt = (
            select(nbr.name, nbr.distance_m)
            .where(nbr.listing_id == listing_id)
            .order_by(nbr.rank)
            .limit(_PARKS_TOP_N)
        )
        rows = (await self.db.execute(stmt)).all()
        return [
            NearestPark(name=row.name or "", distance_m=row.distance_m)
            for row in rows
        ]

    async def _fetch_playground(self, listing_id: uuid.UUID) -> NearestPlayground | None:
        nbr = ListingNearbyPlayground
        stmt = (
            select(nbr.name, nbr.distance_m)
            .where(nbr.listing_id == listing_id)
            .order_by(nbr.rank)
            .limit(_PLAYGROUNDS_TOP_N)
        )
        row = (await self.db.execute(stmt)).one_or_none()
        if row is None:
            return None
        return NearestPlayground(name=row.name or "", distance_m=row.distance_m)

    async def _fetch_hospitals(self, listing_id: uuid.UUID) -> list[NearestHospital]:
        nbr = ListingNearbyHospital
        stmt = (
            select(nbr.name, nbr.tier, nbr.distance_m)
            .where(nbr.listing_id == listing_id)
            .order_by(nbr.rank)
            .limit(_HOSPITALS_TOP_N)
        )
        rows = (await self.db.execute(stmt)).all()
        return [
            NearestHospital(
                name=row.name or "",
                tier=row.tier,
                distance_m=row.distance_m,
            )
            for row in rows
        ]

    async def _fetch_water(self, listing_id: uuid.UUID) -> NearestWater | None:
        nbr = ListingNearbyWater
        stmt = (
            select(nbr.name, nbr.water_kind, nbr.distance_m)
            .where(nbr.listing_id == listing_id)
            .order_by(nbr.rank)
            .limit(_WATER_TOP_N)
        )
        row = (await self.db.execute(stmt)).one_or_none()
        if row is None:
            return None
        return NearestWater(
            name=row.name or "",
            water_kind=row.water_kind,
            distance_m=row.distance_m,
        )

    # ---- Listing + scalar/field projection (no junction tables) ----

    @staticmethod
    def _project_listing(
        listing: Listing, lgc: ListingGeoContext | None
    ) -> ListingDetail:
        """Build ListingDetail from listing + gold scalar/field row.

        POI sets (nearest_transit_stops / schools / parks / playground /
        hospitals / water) are filled by the caller from junction-table
        fetches; this stub leaves them empty so a missing-gold listing
        still gets a valid ListingDetail.
        """
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

        # Scalar / field geo-context. Bucket labels applied at construction
        # time so consumers see fresh labels even if thresholds changed
        # since the gold rebuild.
        detail.school_catchment = (
            SchoolCatchmentInfo(**lgc.school_catchment)
            if lgc.school_catchment
            else None
        )
        detail.noise = _build_noise_profile(lgc.noise_profile)
        detail.greenery = _build_greenery_profile(lgc.greenery_profile)
        detail.density = _build_density_profile(lgc.density_profile)
        detail.mss = MssProfile(**lgc.mss_profile) if lgc.mss_profile else None
        detail.disabled_parking_count = lgc.disabled_parking_count or 0
        return detail


# ---------------------------------------------------------------------------
# JSONB → Pydantic helpers (scalar/field profiles only — POI sets are
# assembled from junction-table row tuples directly above).
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
