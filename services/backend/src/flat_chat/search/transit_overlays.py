"""TransitOverlayService — resolve a transit line name to a drawable geometry.

Agent-only, display-only. Turns a human line name ("U7", "S41", "M10") into a
`MapOverlay` carrying the line's shape so the agent can draw it on the map
("show me the U7", or auto-drawing the line a search filters on). Sits beside
`PlaceService` as the second agent-only overlay-geometry resolver in `search/`.

This is DELIBERATELY separate from the search-side transit filter. "Near the
U8" means near a *stop served by* the U8 (matched via `listings_nearby_transit`,
a stop-proximity junction) — NOT near the line's centerline. A line in the
search-near gazetteer would invite a wrong `ST_DWithin` against tunnel
midpoints. So transit geometry lives here, used only for rendering, and the
two never mix. See agent-compound-docs/decisions/map-overlays.md.

VBB GTFS models one human "line" as MANY routes (`S3` has 8 `route_id`s, `S7`
has 5) and many trip patterns. Naively `ST_Collect`-ing every shape that shares
a `short_name` superimposes short-turns, branches, and even cross-mode
replacement services (an `S7` route with `route_type=700`, i.e. a bus) — the
loops/divergence reported in issue #29. So we:

  1. keep only routes of the line's DOMINANT mode (most-common `route_type`),
     dropping the replacement-bus impostor;
  2. pick the LONGEST shape per direction (drops short-turn sub-segments);
  3. `ST_Collect` the ≤2 winners, simplify, and emit GeoJSON.

Tradeoff: longest-per-direction collapses genuine branches to the trunk spine.
For a display overlay that reads better than a superimposed bundle, and most VBB
short-turns are sub-segments of the full pattern anyway. Line name match is
case-insensitive so "u7" resolves the same as "U7".
"""

from __future__ import annotations

import json
import logging
from collections import Counter

from geoalchemy2 import Geography
from geoalchemy2 import functions as geo_func
from sqlalchemy import cast, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from flat_chat.listings.models import TransitRoute, TransitRouteShape, TransitStop
from flat_chat.listings.overlays import (
    OVERLAY_COORD_DIGITS,
    OVERLAY_SIMPLIFY_TOLERANCE,
    OVERLAY_STATION_SNAP_M,
    MapOverlay,
    OverlayOrigin,
    OverlayPoint,
)

logger = logging.getLogger(__name__)

# A winning shape, identified by its (route_id, direction_id) primary key.
ShapeKey = tuple[str, int]


class TransitOverlayService:
    """Resolve a transit line name to its map geometry. Agent-only."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def route_geometry(
        self, line: str, *, origin: OverlayOrigin = "search"
    ) -> MapOverlay | None:
        """Return a `MapOverlay` for transit line `line` ("U7"), or None.

        Resolves to one representative spine per direction in the line's
        dominant mode (see module docstring). None when the name is empty or no
        route/shape matches (e.g. a bus line with no shape) — the agent then
        just doesn't draw a line.
        """
        name = (line or "").strip()
        if not name:
            return None

        routes = (
            await self.db.execute(
                select(
                    TransitRoute.route_id,
                    TransitRoute.route_type,
                    TransitRoute.short_name,
                ).where(func.upper(TransitRoute.short_name) == name.upper())
            )
        ).all()
        if not routes:
            return None

        # Dominant mode = most-common route_type; tie-break the smallest type id
        # for determinism. Drops impostors like an S7 replacement bus (700)
        # mixed with the S-Bahn routes (109).
        counts = Counter(r.route_type for r in routes)
        dominant = min(counts, key=lambda t: (-counts[t], t))
        route_ids = [r.route_id for r in routes if r.route_type == dominant]
        label = next((r.short_name for r in routes if r.short_name), name)

        winner_keys = await self._longest_shape_per_direction(route_ids)
        if not winner_keys:
            return None

        shape_filter = tuple_(
            TransitRouteShape.route_id, TransitRouteShape.direction_id
        ).in_(winner_keys)
        geojson = (
            await self.db.execute(
                select(
                    geo_func.ST_AsGeoJSON(
                        geo_func.ST_SimplifyPreserveTopology(
                            geo_func.ST_Collect(TransitRouteShape.geom),
                            OVERLAY_SIMPLIFY_TOLERANCE,
                        ),
                        OVERLAY_COORD_DIGITS,
                    )
                ).where(shape_filter)
            )
        ).scalar()
        if geojson is None:
            return None

        return MapOverlay(
            id=f"transit_line:{label}",
            kind="transit_line",
            label=label,
            geojson=json.loads(geojson),
            origin=origin,
            points=await self._stations(label, winner_keys),
        )

    async def _longest_shape_per_direction(
        self, route_ids: list[str]
    ) -> list[ShapeKey]:
        """Pick the longest shape per direction across `route_ids`.

        Returns the winning `(route_id, direction_id)` keys (≤2 in practice).
        Length is compared in degrees (`ST_Length` on a 4326 geometry) — fine
        for an argmax within one line, all shapes share a latitude band, so the
        degree ordering matches the metre ordering and we skip a geography cast.
        """
        candidates = (
            await self.db.execute(
                select(
                    TransitRouteShape.route_id,
                    TransitRouteShape.direction_id,
                    geo_func.ST_Length(TransitRouteShape.geom).label("length"),
                ).where(TransitRouteShape.route_id.in_(route_ids))
            )
        ).all()

        winners: dict[int, ShapeKey] = {}
        best_len: dict[int, float] = {}
        for c in candidates:
            if c.direction_id not in best_len or c.length > best_len[c.direction_id]:
                best_len[c.direction_id] = c.length
                winners[c.direction_id] = (c.route_id, c.direction_id)
        return list(winners.values())

    async def _stations(
        self, label: str, winner_keys: list[ShapeKey]
    ) -> list[OverlayPoint]:
        """Stations served by line `label`, restricted AND snapped to the spine.

        A stop must (a) claim the line in its `lines_served` array AND (b) lie
        within `OVERLAY_STATION_SNAP_M` of the resolved geometry. (a) alone
        over-selects: `lines_served` is aggregated over EVERY trip pattern while
        the drawn shape is one representative per direction, so off-shape
        variant stops — and cross-region duplicates of an ambiguous line number
        — would otherwise be drawn (issue #30). (b) keeps the dots on the line.

        The emitted coordinate is the station SNAPPED onto the spine
        (`ST_ClosestPoint`), not its raw position, so every dot sits exactly on
        the drawn line rather than beside it. Tradeoff: a genuinely offset
        station at a large interchange is nudged onto the line — acceptable for a
        schematic line overlay. Planar snap on 4326 is correct here (the stop is
        already within 250 m); the distance gate keeps its geography cast.

        Returns `[]` when nothing matches. Coordinates are rounded to the same
        precision as the line geometry to keep the state snapshot small.
        """
        membership = func.array_position(TransitStop.lines_served, label).is_not(None)
        if not winner_keys:
            # Defensive: route_geometry never calls us without winners, but if
            # it did, fall back to membership-only + raw coords (nothing to snap
            # to) rather than drop every stop.
            where_clause = membership
            point = TransitStop.geom
        else:
            shape_geom = (
                select(geo_func.ST_Collect(TransitRouteShape.geom))
                .where(
                    tuple_(
                        TransitRouteShape.route_id, TransitRouteShape.direction_id
                    ).in_(winner_keys)
                )
                .scalar_subquery()
            )
            where_clause = membership & geo_func.ST_DWithin(
                cast(TransitStop.geom, Geography),
                cast(shape_geom, Geography),
                OVERLAY_STATION_SNAP_M,
            )
            point = geo_func.ST_ClosestPoint(shape_geom, TransitStop.geom)

        stmt = select(
            TransitStop.name.label("name"),
            geo_func.ST_X(point).label("lon"),
            geo_func.ST_Y(point).label("lat"),
        ).where(where_clause)
        rows = (await self.db.execute(stmt)).all()
        return [
            OverlayPoint(
                label=r.name,
                lon=round(r.lon, OVERLAY_COORD_DIGITS),
                lat=round(r.lat, OVERLAY_COORD_DIGITS),
            )
            for r in rows
        ]
