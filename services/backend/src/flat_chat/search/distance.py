"""DistanceService — straight-line distance from a place to each listing.

The distance-LENS provider: given the active result-set markers and a
`DistanceLens`, returns `{marker_id: metres}` measured geometry-precise to the
place's exact shape (a line/polygon, not just its centroid) via PostGIS
`ST_Distance`. Mirrors `SearchService`'s `near_place_ref` `ST_DWithin` path but
returns the distance instead of filtering — no routing engine involved, which is
what makes the lens abstraction demonstrably not coupled to travel-time.

Agent-only (like `SearchService`/`PlaceService`). Same
`resolve(markers, lens) -> {id: value}` shape as `RoutingService.resolve`, so the
lens layer treats both as interchangeable providers.
"""

from __future__ import annotations

import logging
import uuid

from geoalchemy2 import Geography
from geoalchemy2 import functions as geo_func
from sqlalchemy import Float, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from flat_chat.listings.context import Marker
from flat_chat.listings.lenses import ActiveLens, DistanceLens
from flat_chat.listings.models import Listing, named_places
from flat_chat.search.service import _parse_place_ref

logger = logging.getLogger(__name__)


class DistanceService:
    """Bird's-eye distance from a named place to each listing (one SQL query)."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def resolve(
        self, markers: list[Marker], lens: ActiveLens
    ) -> dict[str, float]:
        """`{marker_id: metres}` straight-line to the lens anchor's geometry.

        Implements the `LensValueProvider` Protocol (hence the `ActiveLens` param);
        the lens layer only ever routes a `distance` lens here, so narrow to
        `DistanceLens` up front. Distance is to the resolved SHAPE (correct for the
        Spree LINE and the
        TU-campus POLYGON), matching the `near_place_ref` search filter. Markers
        with no gold `location`, non-UUID ids, or an unknown/garbage
        `near_place_ref` are simply absent from the dict. One query over the
        result-set ids."""
        assert isinstance(lens, DistanceLens)
        if not markers or lens.near_place_ref is None:
            return {}
        parsed = _parse_place_ref(lens.near_place_ref)
        if parsed is None:
            return {}
        kind, src_id = parsed

        uids: list[uuid.UUID] = []
        for m in markers:
            try:
                uids.append(uuid.UUID(str(m.id)))
            except ValueError:
                logger.debug("distance resolve: skipping non-UUID id %r", m.id)
        if not uids:
            return {}

        # Scalar subquery: the resolved geometry for this place_ref via the
        # mapped `world.named_places` view (constant `kind` prunes the UNION to
        # one branch; `src_id` hits that base table's PK). Bound params only.
        np = named_places.c
        geom_subq = (
            select(np.geom)
            .where(np.kind == kind, np.src_id == src_id)
            .scalar_subquery()
        )
        distance_m = cast(
            geo_func.ST_Distance(
                cast(Listing.location, Geography),
                cast(geom_subq, Geography),
            ),
            Float,
        ).label("m")
        rows = (
            await self.db.execute(
                select(Listing.id, distance_m).where(Listing.id.in_(uids))
            )
        ).all()
        return {str(r.id): float(r.m) for r in rows if r.m is not None}
