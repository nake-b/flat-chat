from __future__ import annotations

import logging

import pandas as pd
from geoalchemy2 import Geography
from geoalchemy2 import functions as geo_func
from pgvector.sqlalchemy import Vector
from pydantic_ai import Embedder
from sqlalchemy import cast, func, or_, select
from sqlalchemy.orm import Session

from flat_chat.search.models import Listing
from flat_chat.search.schemas import SearchParams

logger = logging.getLogger(__name__)

RESULT_COLUMNS = [
    "id",
    "title",
    "price_warm_eur",
    "price_cold_eur",
    "rooms",
    "area_sqm",
    "district",
    "address",
    "floor",
    "listing_type",
    "available_from",
    "source_url",
    "latitude",
    "longitude",
]


class SearchService:
    def __init__(self, db: Session, embedder: Embedder | None = None):
        self.db = db
        self.embedder = embedder

    async def search(self, params: SearchParams) -> pd.DataFrame:
        stmt = select(Listing)

        if params.price_warm_max is not None:
            stmt = stmt.where(Listing.price_warm_eur <= params.price_warm_max)

        if params.rooms_min is not None:
            stmt = stmt.where(Listing.rooms >= params.rooms_min)

        if params.rooms_max is not None:
            stmt = stmt.where(Listing.rooms <= params.rooms_max)

        if params.area_sqm_min is not None:
            stmt = stmt.where(Listing.area_sqm >= params.area_sqm_min)

        if params.floor_min is not None:
            stmt = stmt.where(Listing.floor >= params.floor_min)

        if params.listing_type is not None:
            stmt = stmt.where(Listing.listing_type == params.listing_type)

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

        if params.query and self.embedder:
            embedding = await self._embed(params.query)
            distance = Listing.description_embedding.cosine_distance(
                cast(embedding, Vector(1024))
            )
            stmt = stmt.add_columns(distance.label("similarity_score"))
            stmt = stmt.order_by(distance)
        elif sort_by_effective == "price":
            stmt = stmt.order_by(Listing.price_warm_eur.asc().nulls_last())
        elif sort_by_effective == "area":
            stmt = stmt.order_by(Listing.area_sqm.desc().nulls_last())
        else:
            stmt = stmt.order_by(Listing.created_at.desc())

        stmt = stmt.limit(params.limit)

        result = self.db.execute(stmt)
        rows = result.all()

        if not rows:
            return pd.DataFrame(columns=RESULT_COLUMNS + ["similarity_score"])

        has_score = params.query and self.embedder
        records = []
        for row in rows:
            listing = row[0]
            record = {col: getattr(listing, col) for col in RESULT_COLUMNS}
            score = round(1 - float(row[1]), 4) if has_score else None
            record["similarity_score"] = score
            records.append(record)

        return pd.DataFrame(records)

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
