"""DistanceService — straight-line distance from a place to each listing.

The neutral core is `distances(markers, place_ref)`: returns `{marker_id: metres}`
measured geometry-precise to the place's exact shape (a line/polygon, not just its
centroid) via PostGIS `ST_Distance`. Mirrors `SearchService`'s `near_place_ref`
`ST_DWithin` path but returns the distance instead of filtering — no routing engine
involved, which is what makes the lens abstraction demonstrably not coupled to
travel-time.

`resolve(markers, lens)` is a thin `LensValueProvider` adapter over it (unpacks the
`DistanceLens`'s `near_place_ref`), so the lens layer treats this and
`RoutingService` as interchangeable providers; the point-to-point proximity tools
call `distances` directly without constructing a lens.

Agent-only (like `SearchService`/`PlaceService`).
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

    async def distances(
        self, markers: list[Marker], place_ref: str
    ) -> dict[str, float]:
        """`{marker_id: metres}` straight-line to a named place's geometry — the
        neutral core (a `place_ref`, no lens).

        Distance is to the resolved SHAPE (correct for the Spree LINE and the
        TU-campus POLYGON), matching the `near_place_ref` search filter. Markers
        with no gold `location`, non-UUID ids, or an unknown/garbage `place_ref`
        are simply absent from the dict. One query over the marker ids."""
        if not markers:
            return {}
        parsed = _parse_place_ref(place_ref)
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

    async def resolve(
        self, markers: list[Marker], lens: ActiveLens
    ) -> dict[str, float]:
        """`LensValueProvider` adapter over `distances` — `{marker_id: metres}`.

        The lens layer only ever routes a `distance` lens here, so narrow to
        `DistanceLens` up front and unpack its `near_place_ref` into the core call
        (a distance lens with no place_ref has nothing to measure to)."""
        assert isinstance(lens, DistanceLens)
        if lens.near_place_ref is None:
            return {}
        return await self.distances(markers, lens.near_place_ref)
