"""PlaceService — trigram resolution over the `world.named_places` gazetteer.

Agent-only (like `SearchService`): the `locate_place` tool calls `locate()`
to turn a free-text place name ("TU Berlin", "the Spree", "Tiergarten") into
a small set of candidate `place_ref` tokens. The agent picks one and passes
it back as `search_apartments(near_place_ref=…)`, which resolves the exact
geometry server-side (see `SearchService._apply_listing_filters`).

`world.named_places` is an ingestion-owned VIEW (created in the 0007
migration) that `UNION ALL`s the named source tables and composes the opaque
`place_ref` (`'<kind>:<src_id>'`). The view owns the table↔kind mapping; this
service never references the underlying tables.

Resolution uses `pg_trgm`: `name % :q` (the `%` similarity operator, served
by the per-base-table GIN trigram indexes) plus `similarity(name, :q)` for
ranking. `centroid` lat/lon are for agent display only — the actual distance
search uses the full geometry, not the centroid.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from geoalchemy2 import Geography
from geoalchemy2 import functions as geo_func
from sqlalchemy import cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flat_chat.listings.models import named_places
from flat_chat.listings.overlays import (
    OVERLAY_CLUSTER_RADIUS_M,
    OVERLAY_COORD_DIGITS,
    OVERLAY_SIMPLIFY_TOLERANCE,
    OVERLAY_SNAP_RADIUS_M,
    MapOverlay,
    OverlayOrigin,
)

logger = logging.getLogger(__name__)

# A name search returns at most this many candidates for the agent to pick
# from — small enough to stay cheap in the prompt, large enough to
# disambiguate (e.g. several "Stadtpark"s).
LOCATE_LIMIT = 5


@dataclass(slots=True, kw_only=True)
class PlaceCandidate:
    """One gazetteer hit. Plain stdlib dataclass (not a Pydantic model) — it's
    only ever formatted into prose by `locate_place`, never serialized to the
    frontend."""

    place_ref: str
    kind: str
    name: str | None
    description: str | None
    lat: float | None
    lon: float | None


class PlaceService:
    """Resolve named places by trigram similarity. Agent-only."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def locate(self, name: str) -> list[PlaceCandidate]:
        """Return up to LOCATE_LIMIT candidates whose name fuzzy-matches `name`.

        Empty / whitespace-only input returns []. Ordered by descending
        trigram similarity. `lat`/`lon` are the geometry centroid (display
        only — the real search uses the full geometry via `near_place_ref`).
        """
        q = (name or "").strip()
        if not q:
            return []

        np = named_places.c
        # `%` is the pg_trgm similarity operator (predicate pushdown hits the
        # per-base-table GIN trgm indexes); `similarity(...)` gives the score
        # to order by. Centroid via ST_Centroid so a line/polygon still yields
        # a single display point.
        centroid = geo_func.ST_Centroid(np.geom)
        stmt = (
            select(
                np.place_ref,
                np.kind,
                np.name,
                np.description,
                geo_func.ST_Y(centroid).label("lat"),
                geo_func.ST_X(centroid).label("lon"),
            )
            .where(np.name.op("%")(q))
            # Tiebreak on geometry richness so a polygon/line beats a coincident
            # point at equal name-match (ST_Dimension: polygon=2, line=1,
            # point=0). Without it, a seed-alias POINT sitting on top of real
            # building footprints can win an exact-name tie and the agent draws
            # a dot instead of a shape. Helps near_place_ref search too (a
            # footprint is a better ST_DWithin target than a point).
            .order_by(
                func.similarity(np.name, q).desc(),
                geo_func.ST_Dimension(np.geom).desc(),
            )
            .limit(LOCATE_LIMIT)
        )
        rows = (await self.db.execute(stmt)).all()
        return [
            PlaceCandidate(
                place_ref=r.place_ref,
                kind=r.kind,
                name=r.name,
                description=r.description,
                lat=r.lat,
                lon=r.lon,
            )
            for r in rows
        ]

    async def anchor_point(self, place_ref: str) -> tuple[str, float, float] | None:
        """Resolve a `place_ref` to `(label, lat, lon)` — the routing anchor.

        Returns the place's name and its geometry centroid (a single point even
        for a line/polygon, via `ST_Centroid`). Used by `apply_travel_time` to
        feed the OSRM/MOTIS engines. `None` for an unknown/garbage ref. The
        centroid is a fine anchor: seed-alias points sit on their target, and a
        polygon's centroid is its middle — both snap to the nearest road/stop at
        the engine."""
        from .service import _parse_place_ref  # same package; no import cycle

        parsed = _parse_place_ref(place_ref)
        if parsed is None:
            return None
        kind, src_id = parsed

        np = named_places.c
        centroid = geo_func.ST_Centroid(np.geom)
        row = (
            await self.db.execute(
                select(
                    np.name,
                    geo_func.ST_Y(centroid).label("lat"),
                    geo_func.ST_X(centroid).label("lon"),
                )
                .where(np.kind == kind, np.src_id == src_id)
                .limit(1)
            )
        ).first()
        if row is None or row.lat is None or row.lon is None:
            return None
        return (row.name or place_ref, row.lat, row.lon)

    async def overlay_geometry(
        self, place_ref: str, *, origin: OverlayOrigin = "search"
    ) -> MapOverlay | None:
        """Resolve a `place_ref` to a drawable `MapOverlay`, or None if unknown.

        Two-step:

        1. **Snap.** If the hit is a representative POINT (a seed alias like
           "TU Berlin" / "Görli"), snap to the nearest footprint (polygon/line,
           ANY kind) within `OVERLAY_SNAP_RADIUS_M` and use it as the anchor —
           the curated pin sits ON its target, so the nearest footprint IS the
           place (the Hauptgebäude building, the Görlitzer Park polygon). The
           building/park name never matches the alias, so proximity (not name)
           is what finds it. No footprint near → fall back to the point.
        2. **Cluster-union.** Union the anchor's same-kind, same-name footprints
           within `OVERLAY_CLUSTER_RADIUS_M` (a campus fragmented into many
           identically-named rows → its local cluster; a unique place → itself),
           keeping the richest dimension.

        The chip label stays the name the user referenced. `_parse_place_ref`
        is the shared, fail-closed token parser — a garbage ref yields None.
        """
        from .service import _parse_place_ref  # same package; no import cycle

        parsed = _parse_place_ref(place_ref)
        if parsed is None:
            return None
        kind, src_id = parsed

        np = named_places.c
        base = (
            await self.db.execute(
                select(np.name, geo_func.ST_Dimension(np.geom).label("dim"))
                .where(np.kind == kind, np.src_id == src_id)
                .limit(1)
            )
        ).first()
        if base is None:
            return None
        label = base.name or place_ref

        # Step 1 — snap a marker point to the footprint it sits on.
        anchor_kind, anchor_src_id = kind, src_id
        if base.dim == 0:
            base_geom = (
                select(np.geom).where(np.kind == kind, np.src_id == src_id).limit(1)
            ).scalar_subquery()
            snap = (
                await self.db.execute(
                    select(np.kind.label("kind"), np.src_id.label("src_id"))
                    .where(
                        geo_func.ST_Dimension(np.geom) >= 1,
                        geo_func.ST_DWithin(
                            cast(np.geom, Geography),
                            cast(base_geom, Geography),
                            OVERLAY_SNAP_RADIUS_M,
                        ),
                    )
                    .order_by(
                        geo_func.ST_Distance(
                            cast(np.geom, Geography), cast(base_geom, Geography)
                        )
                    )
                    .limit(1)
                )
            ).first()
            if snap is not None:
                anchor_kind, anchor_src_id = snap.kind, snap.src_id

        # Step 2 — cluster-union the (possibly snapped) anchor's same-name,
        # same-kind footprints within the cluster radius.
        anchor_name = (
            select(np.name)
            .where(np.kind == anchor_kind, np.src_id == anchor_src_id)
            .limit(1)
        ).scalar_subquery()
        anchor_geom = (
            select(np.geom)
            .where(np.kind == anchor_kind, np.src_id == anchor_src_id)
            .limit(1)
        ).scalar_subquery()
        cluster = (
            select(
                np.geom.label("geom"),
                geo_func.ST_Dimension(np.geom).label("dim"),
            )
            .where(
                np.kind == anchor_kind,
                np.name.is_not_distinct_from(anchor_name),
                geo_func.ST_DWithin(
                    cast(np.geom, Geography),
                    cast(anchor_geom, Geography),
                    OVERLAY_CLUSTER_RADIUS_M,
                ),
            )
            .cte("cluster")
        )
        # Keep only the richest-dimension members (polygons over a coincident
        # alias point, etc.). ST_Union (not ST_Collect) dissolves them into a
        # homogeneous Polygon/MultiPolygon (or Line) — ST_Collect would emit a
        # GeometryCollection when mixing POLYGON + MULTIPOLYGON rows (ALKIS
        # footprints are a mix), which the frontend can't classify.
        max_dim = select(func.max(cluster.c.dim)).scalar_subquery()
        geojson_expr = geo_func.ST_AsGeoJSON(
            geo_func.ST_SimplifyPreserveTopology(
                geo_func.ST_Union(cluster.c.geom), OVERLAY_SIMPLIFY_TOLERANCE
            ),
            OVERLAY_COORD_DIGITS,
        )
        stmt = select(geojson_expr.label("geojson")).where(cluster.c.dim == max_dim)
        row = (await self.db.execute(stmt)).first()
        if row is None or row.geojson is None:
            return None

        return MapOverlay(
            id=f"place:{place_ref}",
            kind="place",
            label=label,
            geojson=json.loads(row.geojson),
            origin=origin,
        )
