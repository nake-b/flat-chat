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
import re

from geoalchemy2 import Geography
from geoalchemy2 import functions as geo_func
from sqlalchemy import cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from flat_chat.listings.context import (
    OVERLAY_CLUSTER_RADIUS_M,
    OVERLAY_COORD_DIGITS,
    OVERLAY_SIMPLIFY_TOLERANCE,
    MapOverlay,
    OverlayOrigin,
)
from flat_chat.listings.models import named_places

logger = logging.getLogger(__name__)

# A name search returns at most this many candidates for the agent to pick
# from — small enough to stay cheap in the prompt, large enough to
# disambiguate (e.g. several "Stadtpark"s).
LOCATE_LIMIT = 5

# Seed-alias backstops (e.g. "TU Berlin", "Görli", "Kotti") store their target
# in the description as `Alias → <Canonical Name> (optional note).`. The
# canonical name is the string between "Alias → " and the first " (" or ".".
# When an overlay resolves to such an alias, we also union footprints named
# `<Canonical Name>` so the short alias draws the real (often fragmented) place
# — e.g. "TU Berlin" → the "Technische Universität Berlin" campus footprints,
# not just the alias point. Fails closed: a description without this exact
# shape yields None and the alias falls back to its own name only.
_ALIAS_CANONICAL_RE = re.compile(r"^\s*Alias\s*→\s*(?P<name>.+?)\s*(?:\(|\.|$)")


def _alias_canonical_name(description: str | None) -> str | None:
    """Extract the canonical place name from a seed-alias description, or None."""
    if not description:
        return None
    match = _ALIAS_CANONICAL_RE.match(description)
    if match is None:
        return None
    name = match.group("name").strip()
    return name or None


class PlaceCandidate:
    """One gazetteer hit. Plain dataclass-style container (not a Pydantic
    model) — it's only ever formatted into prose by `locate_place`, never
    serialized to the frontend."""

    __slots__ = ("place_ref", "kind", "name", "description", "lat", "lon")

    def __init__(
        self,
        *,
        place_ref: str,
        kind: str,
        name: str | None,
        description: str | None,
        lat: float | None,
        lon: float | None,
    ) -> None:
        self.place_ref = place_ref
        self.kind = kind
        self.name = name
        self.description = description
        self.lat = lat
        self.lon = lon


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

    async def overlay_geometry(
        self, place_ref: str, *, origin: OverlayOrigin = "search"
    ) -> MapOverlay | None:
        """Resolve a `place_ref` to a drawable `MapOverlay`, or None if unknown.

        Resolves the hit by `kind`+`src_id` (same view pruning the search side
        uses), then UNIONS same-kind footprints within `OVERLAY_CLUSTER_RADIUS_M`
        of it whose name is the hit's name OR — if the hit is a seed alias — its
        canonical target (see `_alias_canonical_name`). So a campus fragmented
        into many identically-named rows draws as one local cluster, a short
        alias ("TU Berlin") draws the real place ("Technische Universität
        Berlin") footprints, and a unique-named place unions to itself.
        Exact-name match keeps unrelated neighbours out (see
        OVERLAY_CLUSTER_RADIUS_M). `_parse_place_ref` is the shared, fail-closed
        token parser — a hallucinated/garbage ref yields None, never a 500.
        """
        from .service import _parse_place_ref  # same package; no import cycle

        parsed = _parse_place_ref(place_ref)
        if parsed is None:
            return None
        kind, src_id = parsed

        np = named_places.c
        # Resolve the hit's own name + description first; the description may
        # carry an "Alias → <canonical>" pointer we also union by.
        meta = (
            await self.db.execute(
                select(np.name, np.description)
                .where(np.kind == kind, np.src_id == src_id)
                .limit(1)
            )
        ).first()
        if meta is None:
            return None
        anchor_name = meta.name
        canonical = _alias_canonical_name(meta.description)
        union_names = [n for n in {anchor_name, canonical} if n is not None]

        # Name predicate: any of the union names (alias own name ∪ canonical),
        # NULL-safe so an unnamed hit still unions same-(NULL)-named rows.
        if union_names:
            name_cond = np.name.in_(union_names)
            if anchor_name is None:
                name_cond = or_(name_cond, np.name.is_(None))
        else:
            name_cond = np.name.is_(None)

        anchor_geom = (
            select(np.geom).where(np.kind == kind, np.src_id == src_id).limit(1)
        ).scalar_subquery()
        # The local cluster: matching-name, same-kind footprints within the
        # radius of the anchor, each tagged with its geometry dimension.
        cluster = (
            select(
                np.geom.label("geom"),
                geo_func.ST_Dimension(np.geom).label("dim"),
            )
            .where(
                np.kind == kind,
                name_cond,
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
            label=anchor_name or place_ref,
            geojson=json.loads(row.geojson),
            origin=origin,
        )
