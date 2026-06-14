from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import pandas as pd
from geoalchemy2 import Geography
from geoalchemy2 import functions as geo_func
from pgvector.sqlalchemy import Vector
from pydantic_ai import Embedder
from sqlalchemy import cast, func, or_, select
from sqlalchemy.orm import Session

from flat_chat.search.buckets import bucket_density, bucket_noise
from flat_chat.search.distances import walk_minutes
from flat_chat.search.geo_context_service import (
    MSS_DYNAMICS_DE_TO_EN,
    MSS_STATUS_DE_TO_EN,
    GeoContextService,
)
from flat_chat.search.geo_filters import ListingContext
from flat_chat.search.models import Listing
from flat_chat.search.schemas import SearchParams

logger = logging.getLogger(__name__)


@dataclass
class ListingWithContext:
    """A single listing plus its full geo-context blob.

    Returned by `SearchService.get_listing_details(listing_id)` and consumed
    by the `get_listing_details` agent tool, which formats the listing fields
    for the LLM prose and mirrors `context` into `UiState.active_listing_context`.
    """

    listing: Listing
    context: ListingContext

# Pandas-facing column names returned to the agent / formatting layer.
# These are intentionally distinct from the DB column names — see _ORM_ATTR
# below — so that the schema (owned by ingestion) and the LLM-facing data
# model can evolve independently.
RESULT_COLUMNS = [
    "id",
    "title",
    # Money
    "price_warm_eur",
    "price_cold_eur",
    "nebenkosten_eur",
    "kaution_eur",
    # Size
    "rooms",
    "bedrooms",
    "area_sqm",
    # Location
    "district",
    "address",
    # Building / availability
    "floor",
    "floors_total",
    "listing_type",
    "available_from",
    # Amenities (chip-row subset on the frontend)
    "wbs_required",
    "is_furnished",
    "has_balcony",
    "has_kitchen",
    "has_elevator",
    "has_garden",
    # Energy
    "heating",
    "energy_consumption_kwh",
    # Listing source
    "lister_type",
    # Outbound
    "source_url",
    # Geo (for the map; UiApartment maps these to lat/lng)
    "latitude",
    "longitude",
    # Geo-context chips — populated by GeoContextService.apply_chips() via
    # LATERAL joins. Always present in the DataFrame (None when the listing
    # has no nearby data or no location). Labels (walk_min, noise_label,
    # density_label, mss_*_label) are derived in Python from the SQL-emitted
    # raw values immediately below in the record-building loop.
    "nearest_transit_line",
    "nearest_transit_m",
    "walk_min_to_transit",
    "nearest_park_name",
    "nearest_park_m",
    "noise_total_lden",
    "noise_label",
    "persons_per_hectare",
    "density_label",
    "mss_status_label",
    "mss_dynamics_label",
]

# Pandas column -> ORM attribute name on Listing. Only entries that differ.
# Identity columns (title, rooms, district, …) omitted.
_ORM_ATTR = {
    "price_warm_eur": "warm_rent_eur",
    "price_cold_eur": "cold_rent_eur",
    "listing_type": "apartment_type",
    "source_url": "listing_url",
}

# Chip column labels emitted by GeoContextService.apply_chips() — must match
# the `.label(...)` calls there. Derived labels (walk_min_to_transit,
# noise_label, density_label, mss_*_label) are computed in Python from these.
_CHIP_COLUMNS_FROM_SQL = (
    "nearest_transit_line",
    "nearest_transit_m",
    "nearest_park_name",
    "nearest_park_m",
    "noise_total_lden",
    "persons_per_hectare",
    "mss_status_de",
    "mss_dynamics_de",
)

# Columns derived in Python after pulling SQL chips — kept out of the loop's
# `record[col] = getattr(listing, ...)` pass and computed explicitly below.
_DERIVED_LABEL_COLUMNS = (
    "walk_min_to_transit",
    "noise_label",
    "density_label",
    "mss_status_label",
    "mss_dynamics_label",
)


class SearchService:
    def __init__(
        self,
        db: Session,
        geo: GeoContextService,
        embedder: Embedder | None = None,
    ):
        self.db = db
        self.geo = geo
        self.embedder = embedder

    async def search(self, params: SearchParams) -> pd.DataFrame:
        stmt = select(Listing)

        # Money
        if params.price_warm_min is not None:
            stmt = stmt.where(Listing.warm_rent_eur >= params.price_warm_min)
        if params.price_warm_max is not None:
            stmt = stmt.where(Listing.warm_rent_eur <= params.price_warm_max)
        if params.price_cold_max is not None:
            stmt = stmt.where(Listing.cold_rent_eur <= params.price_cold_max)

        # Size
        if params.rooms_min is not None:
            stmt = stmt.where(Listing.rooms >= params.rooms_min)
        if params.rooms_max is not None:
            stmt = stmt.where(Listing.rooms <= params.rooms_max)
        if params.bedrooms_min is not None:
            stmt = stmt.where(Listing.bedrooms >= params.bedrooms_min)
        if params.area_sqm_min is not None:
            stmt = stmt.where(Listing.area_sqm >= params.area_sqm_min)
        if params.area_sqm_max is not None:
            stmt = stmt.where(Listing.area_sqm <= params.area_sqm_max)

        # Building / availability
        if params.floor_min is not None:
            stmt = stmt.where(Listing.floor >= params.floor_min)
        if params.floor_max is not None:
            stmt = stmt.where(Listing.floor <= params.floor_max)
        if params.listing_type is not None:
            stmt = stmt.where(Listing.apartment_type == params.listing_type)
        if params.available_by is not None:
            # ISO date string; SQLAlchemy + Postgres cast it to TIMESTAMP for
            # the comparison. We accept the raw string here rather than
            # parsing in this layer so a bad format raises at the DB instead
            # of silently coercing into the wrong half-year.
            stmt = stmt.where(Listing.available_from <= params.available_by)

        # Amenities — tri-state. None is a no-op; True/False both filter.
        if params.wbs_required is not None:
            stmt = stmt.where(Listing.wbs_required == params.wbs_required)
        if params.is_furnished is not None:
            stmt = stmt.where(Listing.is_furnished == params.is_furnished)
        if params.has_balcony is not None:
            stmt = stmt.where(Listing.has_balcony == params.has_balcony)
        if params.has_kitchen is not None:
            stmt = stmt.where(Listing.has_kitchen == params.has_kitchen)
        if params.has_elevator is not None:
            stmt = stmt.where(Listing.has_elevator == params.has_elevator)

        if params.districts:
            district_clauses = [
                Listing.district.ilike(_escape_for_substring(d), escape="\\")
                for d in params.districts
            ]
            stmt = stmt.where(or_(*district_clauses))

        if params.has_images is True:
            stmt = stmt.where(func.jsonb_array_length(Listing.images) > 0)

        if params.near_lat is not None and params.near_lon is not None:
            # Cast both sides to Geography so ST_DWithin's distance is in METERS
            # on the WGS84 spheroid. With plain Geometry(4326) it'd be degrees,
            # making radius_km * 1000 search the whole planet. The functional
            # GiST index on (location::geography) makes this index-aware.
            point = geo_func.ST_SetSRID(
                geo_func.ST_MakePoint(params.near_lon, params.near_lat), 4326
            )
            radius_m = params.radius_km * 1000
            stmt = stmt.where(
                geo_func.ST_DWithin(
                    cast(Listing.location, Geography),
                    cast(point, Geography),
                    radius_m,
                    type_=bool,
                )
            )

        # Geo-context: pre-filter predicates (only those the user set) +
        # always-on chip LATERAL joins. Cheap thanks to GIST indexes.
        stmt = self.geo.apply_filters(stmt, params)
        stmt = self.geo.apply_chips(stmt)

        # Resolve the effective sort. Be explicit when "relevance" can't be
        # honored — the user-facing note is generated in tools.py; here we
        # just log for ops visibility and degrade cleanly.
        sort_by_effective = params.sort_by
        if params.sort_by == "relevance":
            if not params.query:
                logger.info("sort_by=relevance with no query — falling back to recent")
                sort_by_effective = "recent"
            elif self.embedder is None:
                logger.warning(
                    "sort_by=relevance with query but no embedder — "
                    "falling back to recent"
                )
                sort_by_effective = "recent"

        # Compute the similarity column whenever we *can*, so the LLM sees a
        # similarity_score in every result even when it sorted by price/area.
        # ORDER BY is decoupled from this: only relevance uses distance.
        distance = None
        if params.query and self.embedder:
            embedding = await self._embed(params.query)
            distance = Listing.embedding.cosine_distance(
                cast(embedding, Vector(1024))
            )
            stmt = stmt.add_columns(distance.label("similarity_score"))

        if sort_by_effective == "relevance" and distance is not None:
            stmt = stmt.order_by(distance)
        elif sort_by_effective == "price":
            stmt = stmt.order_by(Listing.warm_rent_eur.asc().nulls_last())
        elif sort_by_effective == "area":
            stmt = stmt.order_by(Listing.area_sqm.desc().nulls_last())
        else:
            stmt = stmt.order_by(Listing.ingested_at.desc())

        stmt = stmt.limit(params.limit)

        result = self.db.execute(stmt)
        rows = result.all()

        if not rows:
            return pd.DataFrame(columns=RESULT_COLUMNS + ["similarity_score"])

        has_score = params.query and self.embedder
        records = []
        for row in rows:
            listing = row[0]
            mapping = row._mapping
            # Listing attributes (skip chip columns — they come from `mapping`).
            record = {
                col: getattr(listing, _ORM_ATTR.get(col, col))
                for col in RESULT_COLUMNS
                if col not in _CHIP_COLUMNS_FROM_SQL
                and col not in _DERIVED_LABEL_COLUMNS
            }
            if has_score:
                record["similarity_score"] = round(
                    1 - float(mapping["similarity_score"]), 4
                )
            else:
                record["similarity_score"] = None
            # Chip columns from the LATERAL joins. None-safe via `.get`.
            for chip in _CHIP_COLUMNS_FROM_SQL:
                record[chip] = mapping.get(chip)
            # Derived label columns — Python-side translations of the raw SQL
            # values. Each label is owned by a single helper (walk_minutes,
            # bucket_noise, bucket_density, MSS_*_DE_TO_EN) so the threshold
            # doc remains the single source of truth.
            transit_m = record.get("nearest_transit_m")
            record["walk_min_to_transit"] = (
                walk_minutes(int(transit_m)) if transit_m is not None else None
            )
            record["noise_label"] = bucket_noise(record.get("noise_total_lden"))
            record["density_label"] = bucket_density(
                record.get("persons_per_hectare")
            )
            record["mss_status_label"] = MSS_STATUS_DE_TO_EN.get(
                record.get("mss_status_de") or ""
            )
            record["mss_dynamics_label"] = MSS_DYNAMICS_DE_TO_EN.get(
                record.get("mss_dynamics_de") or ""
            )
            # Drop the German-label columns now that we've translated them —
            # downstream (UiApartment) only consumes the EN labels. Keeping
            # the DE values out of the DataFrame prevents accidental leakage
            # into the LLM-facing surface.
            record.pop("mss_status_de", None)
            record.pop("mss_dynamics_de", None)
            records.append(record)

        return pd.DataFrame(records)

    def get_listing_details(self, listing_id: str) -> ListingWithContext | None:
        """Fetch a listing and its full geo-context blob.

        Returns None if the listing isn't found (the caller — the agent tool
        — surfaces this to the LLM as a not-found message rather than
        raising). Accepts the id as a string (the UiApartment-facing form);
        coerces to UUID before the lookup because Listing.id is typed
        `uuid.UUID(as_uuid=True)` and SQLAlchemy's `db.get` does not coerce
        strings — it silently returns None on a type mismatch.

        Builds the location expression from `listing.latitude` /
        `listing.longitude` rather than handing `listing.location`
        (`WKBElement`) to the geo helpers — when GeoAlchemy2 binds a
        WKBElement inline it produces `ST_GeomFromEWKT('<hex>')`, but the
        binary hex isn't valid EWKT and Postgres rejects with "invalid
        geometry". Rebuilding from lat/lon sidesteps the issue and matches
        the pattern the existing `near_lat`/`near_lon` filter uses.
        """
        try:
            pk = uuid.UUID(listing_id)
        except (TypeError, ValueError):
            return None
        listing = self.db.get(Listing, pk)
        if listing is None:
            return None
        if listing.latitude is None or listing.longitude is None:
            return ListingWithContext(listing=listing, context=ListingContext())
        loc_expr = geo_func.ST_SetSRID(
            geo_func.ST_MakePoint(listing.longitude, listing.latitude),
            4326,
        )
        context = self.geo.context_for(loc_expr)
        return ListingWithContext(listing=listing, context=context)

    async def _embed(self, text: str) -> list[float]:
        assert self.embedder is not None, "_embed called without embedder"
        result = await self.embedder.embed_query(text)
        return [float(x) for x in result.embeddings[0]]


def _escape_for_substring(s: str) -> str:
    """Escape ILIKE wildcards in user-controlled input for a %substring% match.

    Order matters: backslash first so we don't re-escape our own escapes.
    Caller must pass escape="\\" to ilike() so the engine recognises them.
    """
    escaped = s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
