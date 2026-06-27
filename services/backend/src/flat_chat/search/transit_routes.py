"""TransitRouteService — resolve a transit line name to a drawable geometry.

Agent-only, display-only. Turns a human line name ("U7", "S41", "M10") into a
`MapOverlay` carrying the line's shape so the agent can draw it on the map
("show me the U7", or auto-drawing the line a search filters on).

This is DELIBERATELY separate from the search-side transit filter. "Near the
U8" means near a *stop served by* the U8 (matched via `listings_nearby_transit`,
a stop-proximity junction) — NOT near the line's centerline. A line in the
search-near gazetteer would invite a wrong `ST_DWithin` against tunnel
midpoints. So transit geometry lives here, used only for rendering, and the
two never mix. See agent-compound-docs/decisions/map-overlays.md.

The shape is the canonical per-direction LineString from `transit_route_shapes`
(VBB GTFS, collapsed at ingestion). We `ST_Collect` both directions of a line
into one MultiLineString, simplify, and emit GeoJSON. Line name match is
case-insensitive so "u7" resolves the same as "U7".
"""

from __future__ import annotations

import json
import logging

from geoalchemy2 import functions as geo_func
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flat_chat.listings.context import (
    OVERLAY_COORD_DIGITS,
    OVERLAY_SIMPLIFY_TOLERANCE,
    MapOverlay,
    OverlayOrigin,
)
from flat_chat.listings.models import TransitRoute, TransitRouteShape

logger = logging.getLogger(__name__)


class TransitRouteService:
    """Resolve a transit line name to its map geometry. Agent-only."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def route_geometry(
        self, line: str, *, origin: OverlayOrigin = "search"
    ) -> MapOverlay | None:
        """Return a `MapOverlay` for transit line `line` ("U7"), or None.

        Collects every direction's canonical shape for routes whose
        `short_name` matches `line` (case-insensitive) into one geometry. None
        when the name is empty or no route/shape matches (e.g. a bus line with
        no shape) — the agent then just doesn't draw a line.
        """
        name = (line or "").strip()
        if not name:
            return None

        geojson_expr = geo_func.ST_AsGeoJSON(
            geo_func.ST_SimplifyPreserveTopology(
                geo_func.ST_Collect(TransitRouteShape.geom),
                OVERLAY_SIMPLIFY_TOLERANCE,
            ),
            OVERLAY_COORD_DIGITS,
        )
        stmt = (
            select(
                func.min(TransitRoute.short_name).label("label"),
                geojson_expr.label("geojson"),
            )
            .select_from(TransitRouteShape)
            .join(TransitRoute, TransitRoute.route_id == TransitRouteShape.route_id)
            .where(func.upper(TransitRoute.short_name) == name.upper())
        )
        row = (await self.db.execute(stmt)).first()
        if row is None or row.geojson is None:
            return None

        label = row.label or name
        return MapOverlay(
            id=f"transit_line:{label}",
            kind="transit_line",
            label=label,
            geojson=json.loads(row.geojson),
            origin=origin,
        )
