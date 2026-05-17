from __future__ import annotations

import logging

import pandas as pd
from geoalchemy2 import functions as geo_func
from pgvector.sqlalchemy import Vector
from sqlalchemy import cast, func, or_, select
from sqlalchemy.orm import Session

from flat_chat.search.models import Listing
from flat_chat.search.schemas import SearchFilters

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
    def __init__(self, db: Session, embedder=None):
        self.db = db
        self.embedder = embedder

    async def search(self, filters: SearchFilters) -> pd.DataFrame:
        stmt = select(Listing)

        if filters.price_warm_max is not None:
            stmt = stmt.where(Listing.price_warm_eur <= filters.price_warm_max)

        if filters.rooms_min is not None:
            stmt = stmt.where(Listing.rooms >= filters.rooms_min)

        if filters.rooms_max is not None:
            stmt = stmt.where(Listing.rooms <= filters.rooms_max)

        if filters.area_sqm_min is not None:
            stmt = stmt.where(Listing.area_sqm >= filters.area_sqm_min)

        if filters.floor_min is not None:
            stmt = stmt.where(Listing.floor >= filters.floor_min)

        if filters.listing_type is not None:
            stmt = stmt.where(Listing.listing_type == filters.listing_type)

        if filters.districts:
            district_clauses = [
                Listing.district.ilike(f"%{d}%") for d in filters.districts
            ]
            stmt = stmt.where(or_(*district_clauses))

        if filters.has_images is True:
            stmt = stmt.where(func.jsonb_array_length(Listing.images) > 0)

        if filters.near_lat is not None and filters.near_lon is not None:
            point = geo_func.ST_SetSRID(
                geo_func.ST_MakePoint(filters.near_lon, filters.near_lat), 4326
            )
            radius_m = filters.radius_km * 1000
            stmt = stmt.where(
                geo_func.ST_DWithin(
                    cast(Listing.location, geo_func.Geometry),
                    point,
                    radius_m,
                    type_=bool,
                )
            )

        if filters.query and self.embedder:
            embedding = await self._embed(filters.query)
            distance = Listing.description_embedding.cosine_distance(
                cast(embedding, Vector(1024))
            )
            stmt = stmt.add_columns(distance.label("similarity_score"))
            stmt = stmt.order_by(distance)
        elif filters.sort_by == "price":
            stmt = stmt.order_by(Listing.price_warm_eur.asc().nulls_last())
        elif filters.sort_by == "area":
            stmt = stmt.order_by(Listing.area_sqm.desc().nulls_last())
        else:
            stmt = stmt.order_by(Listing.created_at.desc())

        stmt = stmt.limit(filters.limit)

        result = self.db.execute(stmt)
        rows = result.all()

        if not rows:
            return pd.DataFrame(columns=RESULT_COLUMNS + ["similarity_score"])

        has_score = filters.query and self.embedder
        records = []
        for row in rows:
            listing = row[0]
            record = {
                col: getattr(listing, col) for col in RESULT_COLUMNS
            }
            score = round(1 - float(row[1]), 4) if has_score else None
            record["similarity_score"] = score
            records.append(record)

        return pd.DataFrame(records)

    async def _embed(self, text: str) -> list[float]:
        vectors = await self.embedder.embed([text])
        return vectors[0]
