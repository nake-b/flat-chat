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

import logging
from dataclasses import dataclass

from geoalchemy2 import functions as geo_func
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flat_chat.listings.models import named_places

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
            .order_by(func.similarity(np.name, q).desc())
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
