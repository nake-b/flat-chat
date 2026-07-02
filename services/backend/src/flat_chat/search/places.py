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

Resolution uses `pg_trgm`: the WHERE widens `name % :q` (similarity) with
`name %> :q` (word similarity) so short abbreviations reach the candidate set,
and ranking goes `priority` (curated campuses) → score bucket → `ST_Dimension`
(area over coincident point) → exact score. Each candidate carries its shape
(area/line/point) and containing neighbourhood so the agent can pick the right
one rather than blindly taking rank #1. `centroid` lat/lon are for agent display
only — the actual distance search uses the full geometry, not the centroid.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from geoalchemy2 import Geography
from geoalchemy2 import functions as geo_func
from sqlalchemy import Numeric, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flat_chat.listings.context import Anchor
from flat_chat.listings.models import named_places, ortsteile
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
# from — small enough to stay cheap in the prompt, large enough to give the
# agent a real menu to disambiguate over (several "Stadtpark"s; the park vs the
# same-named bus stop; the three HU campuses).
LOCATE_LIMIT = 8

# ST_Dimension → a human/agent-legible shape word. The agent uses this to
# prefer an *area* (a park/campus polygon you can search within) over a
# coincident *point* (a same-named transit stop) for "near a green space"-type
# queries. See the `locate_place` docstring + tool protocol.
_GEOM_KIND_BY_DIM = {2: "area", 1: "line", 0: "point"}


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
    # "area" | "line" | "point" — the geometry's dimensionality, so the agent
    # can prefer a searchable area over a coincident point.
    geom_kind: str
    # Containing Berlin neighbourhood (Ortsteil), or None if outside Berlin /
    # unresolved — a locality hint that helps the agent and user tell same-named
    # places apart.
    locality: str | None


class PlaceService:
    """Resolve named places by trigram similarity. Agent-only."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def locate(self, name: str) -> list[PlaceCandidate]:
        """Return up to LOCATE_LIMIT candidates whose name fuzzy-matches `name`.

        Empty / whitespace-only input returns []. Each candidate carries its
        shape (`geom_kind`: area/line/point) and containing neighbourhood
        (`locality`) so the agent can pick the right one for the user's query —
        not just rank #1. `lat`/`lon` are the geometry centroid (display only —
        the real search uses the full geometry via `near_place_ref`).

        Matching + ranking:
          - WHERE widens `name % q` (similarity ≥ 0.3) with `name %> q` (word
            similarity), so short abbreviations ("HU", "TU") and multi-word
            queries reach the candidate set — plain `similarity('HU', 'HU Berlin
            – Campus Mitte')` = 0.13 is below the `%` threshold and would drop
            the row entirely. `%>` only ADMITS rows; it does not drive the rank
            (it flattens to 1.0 for any name containing the query as a word,
            including out-of-Berlin stops).
          - ORDER BY `priority` (curated campuses lead), then a coarse score
            bucket, then `ST_Dimension` (an area beats a coincident point inside
            the bucket — surfaces "Volkspark Hasenheide" over the same-named bus
            stop), then the exact score.
        """
        q = (name or "").strip()
        if not q:
            return []

        np = named_places.c
        centroid = geo_func.ST_Centroid(np.geom)
        dim = geo_func.ST_Dimension(np.geom)
        # Best of full-string similarity and word similarity: full-string ranks
        # full-name queries, word-similarity keeps abbreviations from being
        # buried. Rounded to 1 decimal it forms coarse buckets so the
        # dimension tiebreak (area > point) can fire across near-ties, not only
        # exact ones (issue #38).
        score = func.greatest(
            func.similarity(np.name, q), func.word_similarity(q, np.name)
        )
        # Containing Berlin neighbourhood — correlated point-in-polygon against
        # the ortsteil polygons. None outside Berlin (e.g. VBB stops in
        # Brandenburg) — itself a useful "not local" signal.
        locality_subq = (
            select(ortsteile.c.name)
            .where(geo_func.ST_Intersects(ortsteile.c.geom, centroid))
            .limit(1)
            .scalar_subquery()
        )
        stmt = (
            select(
                np.place_ref,
                np.kind,
                np.name,
                np.description,
                geo_func.ST_Y(centroid).label("lat"),
                geo_func.ST_X(centroid).label("lon"),
                dim.label("dim"),
                locality_subq.label("locality"),
            )
            # `%` (similarity) OR `%>` (word similarity, the commutator of
            # `q <% name`) — see the docstring. Both are served by the
            # per-base-table GIN trgm indexes.
            .where(np.name.op("%")(q) | np.name.op("%>")(q))
            .order_by(
                np.priority.desc(),
                # `round(v, s)` is numeric-only in Postgres; similarity/
                # word_similarity are `real`, so cast before bucketing.
                func.round(cast(score, Numeric), 1).desc(),
                dim.desc(),
                score.desc(),
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
                geom_kind=_GEOM_KIND_BY_DIM.get(r.dim, "point"),
                locality=r.locality,
            )
            for r in rows
        ]

    async def anchor_point(self, place_ref: str) -> Anchor | None:
        """Resolve a `place_ref` to an `Anchor(label, lat, lon)` — the single
        point a travel-time / distance lens routes to.

        A routing engine (OSRM/MOTIS) needs ONE destination coordinate, so we
        reduce the place to a point. Prefer the curated `anchor_geom` (a
        campus's MAIN building) when present — otherwise the footprint centroid,
        which for a sprawling multi-building campus is its middle and can sit far
        from any building. `COALESCE(anchor_geom, ST_Centroid(geom))` gives the
        main building for curated campuses and the centroid for everything else.
        `None` for an unknown/garbage ref.

        (The straight-line DISTANCE lens does NOT use this — it measures to the
        full geometry via `ST_Distance` in `DistanceService`; this anchor only
        feeds the routing engines and the drawn anchor marker.)"""
        from .service import _parse_place_ref  # same package; no import cycle

        parsed = _parse_place_ref(place_ref)
        if parsed is None:
            return None
        kind, src_id = parsed

        np = named_places.c
        anchor = geo_func.ST_Centroid(func.coalesce(np.anchor_geom, np.geom))
        row = (
            await self.db.execute(
                select(
                    np.name,
                    geo_func.ST_Y(anchor).label("lat"),
                    geo_func.ST_X(anchor).label("lon"),
                )
                .where(np.kind == kind, np.src_id == src_id)
                .limit(1)
            )
        ).first()
        if row is None or row.lat is None or row.lon is None:
            return None
        return Anchor(row.name or place_ref, row.lat, row.lon)

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

        # Transit stops are anchor POINTs (a station centroid), not footprints.
        # The snap+cluster-union steps below exist to turn a seed-alias point
        # into the BUILDING/PARK it sits on — exactly the wrong move for a stop
        # (it would draw a nearby building instead of the station). So short-
        # circuit: return the station point itself as the overlay.
        if kind == "transit_stop":
            row = (
                await self.db.execute(
                    select(
                        np.name,
                        geo_func.ST_AsGeoJSON(np.geom, OVERLAY_COORD_DIGITS).label(
                            "geojson"
                        ),
                    )
                    .where(np.kind == kind, np.src_id == src_id)
                    .limit(1)
                )
            ).first()
            if row is None or row.geojson is None:
                return None
            return MapOverlay(
                id=f"place:{place_ref}",
                kind="place",
                label=row.name or place_ref,
                geojson=json.loads(row.geojson),
                origin=origin,
            )

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
