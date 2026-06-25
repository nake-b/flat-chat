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

import json
import logging
import uuid
from typing import Any

from sqlalchemy import select, text
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
)

logger = logging.getLogger(__name__)


# Top-N caps for the detail panel — match the v1 (pre-junction) shape so
# the frontend sees the same number of rows per family.
_TRANSIT_TOP_N = 3
_SCHOOLS_TOP_N = 3
_PARKS_TOP_N = 2
_PLAYGROUNDS_TOP_N = 1
_HOSPITALS_TOP_N = 2
_WATER_TOP_N = 1


# Single round-trip for all six neighbour families. Each column is a
# `json_agg` over an ordered+limited inner subquery (top-N by rank),
# preserving `ORDER BY rank` via `json_agg(... ORDER BY rank)`. The outer
# `SELECT` has no FROM clause, so it always returns exactly one row;
# families with no rows come back as NULL (→ empty list / None on parse).
# `:listing_id` is a bound parameter — never string-interpolated.
_NEIGHBOURS_SQL = text(
    f"""
SELECT
  (SELECT json_agg(t ORDER BY t.rank) FROM (
     SELECT stop_id, name, modes, lines, distance_m, rank
     FROM listings_nearby_transit
     WHERE listing_id = :listing_id
     ORDER BY rank LIMIT {_TRANSIT_TOP_N}
   ) t) AS transit,
  (SELECT json_agg(s ORDER BY s.rank) FROM (
     SELECT name, school_type, distance_m, rank
     FROM listings_nearby_schools
     WHERE listing_id = :listing_id
     ORDER BY rank LIMIT {_SCHOOLS_TOP_N}
   ) s) AS schools,
  (SELECT json_agg(p ORDER BY p.rank) FROM (
     SELECT name, distance_m, rank
     FROM listings_nearby_parks
     WHERE listing_id = :listing_id
     ORDER BY rank LIMIT {_PARKS_TOP_N}
   ) p) AS parks,
  (SELECT json_agg(pg ORDER BY pg.rank) FROM (
     SELECT name, distance_m, rank
     FROM listings_nearby_playgrounds
     WHERE listing_id = :listing_id
     ORDER BY rank LIMIT {_PLAYGROUNDS_TOP_N}
   ) pg) AS playgrounds,
  (SELECT json_agg(h ORDER BY h.rank) FROM (
     SELECT name, tier, distance_m, rank
     FROM listings_nearby_hospitals
     WHERE listing_id = :listing_id
     ORDER BY rank LIMIT {_HOSPITALS_TOP_N}
   ) h) AS hospitals,
  (SELECT json_agg(w ORDER BY w.rank) FROM (
     SELECT name, water_kind, distance_m, rank
     FROM listings_nearby_water
     WHERE listing_id = :listing_id
     ORDER BY rank LIMIT {_WATER_TOP_N}
   ) w) AS water
"""
)


def _as_rows(value: Any) -> list[dict]:
    """Normalise a `json_agg` column into a list of dicts.

    Postgres `json_agg` returns NULL for an empty family → ``None`` here.
    The driver may hand back a Python list already (asyncpg often decodes
    JSON) or a raw JSON string (depends on codec) — guard for both.
    """
    if value is None:
        return []
    if isinstance(value, str):
        value = json.loads(value)
    return list(value)


class ListingService:
    """Direct reads of listing data — agent-callable AND HTTP-callable."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, listing_id: str | uuid.UUID) -> ListingDetail | None:
        """Fetch one listing's full tier-3 detail by ID.

        Returns None if no listing matches — the HTTP route surfaces this
        as a 404; the agent tool returns a "not found" message to the LLM.
        Accepts the id as a string or UUID.

        Reads in two passes: listing + gold scalar/field row, then ONE
        query that fans out all six junction-table families as `json_agg`
        scalar subqueries (top-N by rank per family). Two round-trips
        total versus the old 7 (1 + 6 sequential).
        """
        try:
            uid = uuid.UUID(str(listing_id))
        except ValueError:
            # Almost always a client passing garbage; the route turns None
            # into a clean 404, so debug (not warning) is the right level.
            logger.debug("Rejected non-UUID listing id: %r", listing_id)
            return None

        # Outer (not inner) join: a Listing can exist without a
        # ListingGeoContext row — gold hasn't run yet, or the listing has
        # no coordinates so gold skipped it. An inner join would 404 a real
        # listing that merely lacks enrichment; instead we outer-join and
        # `_project_listing` handles `lgc is None`.
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

        # ONE round-trip: six `json_agg` scalar subqueries, each over an
        # ordered+limited inner select against its `(listing_id, rank)`
        # B-tree. One row back, six JSON-array columns (NULL when empty).
        row = (
            await self.db.execute(_NEIGHBOURS_SQL, {"listing_id": uid})
        ).one()

        transit = _as_rows(row.transit)
        schools = _as_rows(row.schools)
        parks = _as_rows(row.parks)
        playgrounds = _as_rows(row.playgrounds)
        hospitals = _as_rows(row.hospitals)
        water = _as_rows(row.water)

        detail.nearest_transit_stops = [
            NearestTransitStop(
                stop_id=r["stop_id"],
                name=r["name"] or "",
                modes=decode_modes(list(r["modes"] or [])),
                lines=list(r["lines"] or []),
                distance_m=r["distance_m"],
                walk_minutes=walk_minutes(r["distance_m"]),
            )
            for r in transit
        ]
        detail.nearest_schools = [
            NearestSchool(
                name=r["name"] or "",
                school_type=r["school_type"],
                distance_m=r["distance_m"],
            )
            for r in schools
        ]
        detail.nearest_parks = [
            NearestPark(name=r["name"] or "", distance_m=r["distance_m"])
            for r in parks
        ]
        detail.nearest_playground = (
            NearestPlayground(
                name=playgrounds[0]["name"] or "",
                distance_m=playgrounds[0]["distance_m"],
            )
            if playgrounds
            else None
        )
        detail.nearest_hospitals = [
            NearestHospital(
                name=r["name"] or "",
                tier=r["tier"],
                distance_m=r["distance_m"],
            )
            for r in hospitals
        ]
        detail.nearest_water = (
            NearestWater(
                name=water[0]["name"] or "",
                water_kind=water[0]["water_kind"],
                distance_m=water[0]["distance_m"],
            )
            if water
            else None
        )
        return detail

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
# JSONB → Pydantic helpers — parse the `jsonb_build_object(...)` blobs the
# gold ETL writes to `listings_geo_context` (scalar/field profiles only;
# POI sets are assembled from junction-table row tuples directly above).
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
    """Parse the gold `noise_profile` JSONB blob into a typed NoiseProfile,
    applying the fresh bucket label from `listings.labels`."""
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
    """Parse the gold `greenery_profile` JSONB blob into a typed
    GreeneryProfile, applying the fresh bucket label from `listings.labels`."""
    if not blob:
        return None
    green_m2 = blob.get("green_m2_within_300m")
    return GreeneryProfile(
        label=bucket_greenery(green_m2),
        green_m2_within_300m=green_m2,
    )


def _build_density_profile(blob: dict | None) -> DensityProfile | None:
    """Parse the gold `density_profile` JSONB blob into a typed
    DensityProfile, applying the fresh bucket label from `listings.labels`."""
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
