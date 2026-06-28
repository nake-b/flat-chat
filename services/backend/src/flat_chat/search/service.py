"""SearchService — async filter+rank against the gold join.

Design:
  - Joins `listings ⨝ listings_geo_context (⨝ listings_embeddings)` and
    filters with plain B-tree predicates on the gold scalars plus EXISTS
    subqueries against the per-listing junction tables.
  - `search()` returns `(markers, preview_cards, total)`: EVERY match as a
    thin tier-1 `Marker` (≤ MARKER_CAP — the map source + the ordered result
    set), plus the top PREVIEW_N as full tier-2 `ListingCard`s (hot for the
    LLM + the card strip). The rest of the cards hydrate on demand via
    `ListingService.get_cards`.
  - The only spatial predicate is `ST_DWithin` on the listing's own location
    for `near_lat/near_lon` proximity search — a single condition that hits
    the functional GiST index and is cheap.

Marker and preview queries share one filter/sort builder (`_compose`) so
their ordering agrees — the LLM's 1-based indices resolve against the marker
order, and the preview must be a true prefix of it.

Neighbour proximity and the chip scalars are precomputed at gold-ETL time
(see `services/ingestion/src/gold/`). Detail fetches go through
`ListingService.get_detail(id)`; lazy card hydration through `get_cards(ids)`.
"""

from __future__ import annotations

import logging
import re

from geoalchemy2 import Geography
from geoalchemy2 import functions as geo_func
from pgvector.sqlalchemy import Vector
from pydantic_ai import Embedder
from sqlalchemy import ARRAY, Boolean, Integer, Select, Text, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from flat_chat.listings.context import ListingCard, Marker
from flat_chat.listings.labels import encode_modes, resolve_near_spec
from flat_chat.listings.models import (
    Listing,
    ListingEmbedding,
    ListingGeoContext,
    ListingNearbyHospital,
    ListingNearbyKita,
    ListingNearbyPark,
    ListingNearbyPlayground,
    ListingNearbySchool,
    ListingNearbyTransit,
    ListingNearbyWater,
    named_places,
)
from flat_chat.listings.projection import CARD_COLUMNS, row_to_listing_card
from flat_chat.listings.thresholds import (
    DENSITY_MODERATE_MAX,
    DENSITY_SPARSE_MAX,
    GREENERY_LEAFY_MIN_M2,
    NOISE_LIVELY_MAX_LDEN,
    NOISE_QUIET_MAX_LDEN,
)

from .geo_filters import (
    HospitalFilter,
    SchoolFilter,
    TransitFilter,
)
from .schemas import MARKER_CAP, PREVIEW_N, SearchParams

logger = logging.getLogger(__name__)


# SQL LIKE patterns containing `%` or `_` need escaping when we want a
# literal substring match. Mirrors the helper in the old service.
_LIKE_META = re.compile(r"([%_\\])")


def _escape_for_substring(s: str) -> str:
    return f"%{_LIKE_META.sub(r'\\\1', s)}%"


def _parse_place_ref(token: str) -> tuple[str, int] | None:
    """Parse a `locate_place` `place_ref` token into `(kind, src_id)`.

    The token is the opaque `'<kind>:<src_id>'` string the
    `world.named_places` view composes (e.g. `"park:42"`). We parse the
    FORMAT only — split on the first `:`, require a non-empty kind and an
    integer src_id — with zero knowledge of which tables back the view.

    Defensive by contract: any malformed token (no colon, empty kind,
    non-integer id, garbage) returns None so the caller drops the filter
    rather than emitting a query that 500s. The LLM passes these opaquely,
    so a hallucinated token must fail closed.
    """
    if not isinstance(token, str):
        return None
    kind, sep, raw_id = token.partition(":")
    if not sep or not kind or not raw_id:
        return None
    try:
        return kind, int(raw_id)
    except ValueError:
        return None


class SearchService:
    """Filter + rank listings. Returns markers + preview cards. Agent-only.

    HTTP routes don't call this; `ListingService` handles direct reads.
    """

    def __init__(self, db: AsyncSession, embedder: Embedder | None = None):
        self.db = db
        self.embedder = embedder

    async def search(
        self, params: SearchParams
    ) -> tuple[list[Marker], list[ListingCard], int]:
        """Run the search. Returns (markers, preview_cards, total).

        - `markers`: EVERY match (≤ MARKER_CAP) as thin tier-1 markers — the
          map source and the ordered result set the LLM indexes into.
        - `preview_cards`: the top PREVIEW_N as full tier-2 cards.
        - `total`: `len(markers)`, unless the cap binds — then a real COUNT(*).

        Markers and preview share `_compose` so their filters AND ORDER BY are
        identical; the preview is a true prefix of the marker order, which is
        what makes the LLM's 1-based indices line up.
        """
        logger.info(
            "Searching: %s",
            params.model_dump(exclude_defaults=True, exclude_none=True),
        )

        # Resolve sort + (optional) query embedding once; shared by both queries.
        sort_effective = params.sort_by
        embedding: list[float] | None = None
        if params.query and self.embedder:
            embedding = await self._embed(params.query)
        elif params.query and not self.embedder:
            logger.warning(
                "sort_by=relevance with query but no embedder — falling back to recent"
            )
            sort_effective = "recent"
        elif params.sort_by == "relevance" and not params.query:
            logger.info("sort_by=relevance with no query — falling back to recent")
            sort_effective = "recent"

        # Markers — thin projection, every match up to the cap.
        marker_stmt = (
            self._compose(
                params,
                embedding=embedding,
                sort_effective=sort_effective,
                select_cols=(
                    Listing.id,
                    Listing.latitude,
                    Listing.longitude,
                    Listing.warm_rent_eur,
                ),
                add_score=False,
            )
            .where(Listing.latitude.is_not(None), Listing.longitude.is_not(None))
            .limit(MARKER_CAP)
        )
        marker_rows = (await self.db.execute(marker_stmt)).all()
        markers = [
            Marker(
                id=str(r.id),
                lat=r.latitude,
                lng=r.longitude,
                price_warm_eur=r.warm_rent_eur,
            )
            for r in marker_rows
        ]
        logger.info("Found %d markers", len(markers))

        total = len(markers)
        if total == MARKER_CAP:
            total = await self.db.scalar(self._count_statement(params)) or total

        # Preview — top-N full cards (same filters + ORDER BY as the markers).
        preview_stmt = (
            self._compose(
                params,
                embedding=embedding,
                sort_effective=sort_effective,
                select_cols=CARD_COLUMNS,
                add_score=True,
            )
            .where(Listing.latitude.is_not(None), Listing.longitude.is_not(None))
            .limit(PREVIEW_N)
        )
        preview_rows = (await self.db.execute(preview_stmt)).all()
        preview = [
            row_to_listing_card(row, with_score=bool(params.query))
            for row in preview_rows
        ]
        return markers, preview, total

    # ---- Statement composition ----

    def _compose(
        self,
        params: SearchParams,
        *,
        embedding: list[float] | None,
        sort_effective: str,
        select_cols: tuple,
        add_score: bool,
    ) -> Select:
        """Filtered + sorted SELECT over `listings ⨝ listings_geo_context
        (⨝ embeddings)`, parameterised by the columns to project.

        Shared by the marker query (thin cols, no score) and the preview query
        (full card cols + score) so their filter set AND ORDER BY are
        identical. For `sort_by=relevance` that means BOTH carry the embedding
        join + cosine-distance order — it is not filter-only.
        """
        stmt: Select = select(*select_cols).outerjoin(
            ListingGeoContext, ListingGeoContext.listing_id == Listing.id
        )
        stmt = self._apply_listing_filters(stmt, params)
        stmt = self._apply_geo_context_filters(stmt, params)

        distance = None
        if embedding is not None:
            stmt = stmt.outerjoin(
                ListingEmbedding, ListingEmbedding.listing_id == Listing.id
            )
            distance = ListingEmbedding.embedding.cosine_distance(
                cast(embedding, Vector(1024))
            )
            if add_score:
                stmt = stmt.add_columns(distance.label("similarity_score"))

        # Every branch ends with `Listing.id` as a unique, deterministic
        # tie-break. The marker query (LIMIT MARKER_CAP) and the preview query
        # (LIMIT PREVIEW_N) are separate executions over non-unique sort
        # columns; without a unique final key Postgres is free to order tied
        # rows differently between the two, breaking the invariant that the
        # preview is a true PREFIX of the markers (which is what makes the
        # LLM's 1-based indices line up with the cards the user sees).
        if sort_effective == "relevance" and distance is not None:
            # Embedded rows rank by cosine distance; un-embedded rows (distance
            # NULL — the common state) fall to the back and degrade to recency,
            # then id, rather than an arbitrary order.
            stmt = stmt.order_by(
                distance.nulls_last(), Listing.ingested_at.desc(), Listing.id
            )
        elif sort_effective == "price":
            stmt = stmt.order_by(Listing.warm_rent_eur.asc().nulls_last(), Listing.id)
        elif sort_effective == "area":
            stmt = stmt.order_by(Listing.area_sqm.desc().nulls_last(), Listing.id)
        else:
            stmt = stmt.order_by(Listing.ingested_at.desc(), Listing.id)
        return stmt

    def _count_statement(self, params: SearchParams) -> Select:
        """COUNT(*) over the filtered set — only run when the marker cap binds."""
        stmt: Select = (
            select(func.count())
            .select_from(Listing)
            .outerjoin(ListingGeoContext, ListingGeoContext.listing_id == Listing.id)
        )
        stmt = self._apply_listing_filters(stmt, params)
        stmt = self._apply_geo_context_filters(stmt, params)
        return stmt.where(Listing.latitude.is_not(None), Listing.longitude.is_not(None))

    def _apply_listing_filters(self, stmt: Select, params: SearchParams) -> Select:
        """Filters that read directly off the `listings` table."""
        # Money
        if params.price_warm_min is not None:
            stmt = stmt.where(Listing.warm_rent_eur >= params.price_warm_min)
        if params.price_warm_max is not None:
            stmt = stmt.where(Listing.warm_rent_eur <= params.price_warm_max)
        if params.price_cold_max is not None:
            stmt = stmt.where(Listing.cold_rent_eur <= params.price_cold_max)

        # Size
        if params.rooms_min is not None:
            stmt = stmt.where(Listing.rooms >= params.rooms_min)
        if params.rooms_max is not None:
            stmt = stmt.where(Listing.rooms <= params.rooms_max)
        if params.bedrooms_min is not None:
            stmt = stmt.where(Listing.bedrooms >= params.bedrooms_min)
        if params.area_sqm_min is not None:
            stmt = stmt.where(Listing.area_sqm >= params.area_sqm_min)
        if params.area_sqm_max is not None:
            stmt = stmt.where(Listing.area_sqm <= params.area_sqm_max)

        # Building / availability
        if params.floor_min is not None:
            stmt = stmt.where(Listing.floor >= params.floor_min)
        if params.floor_max is not None:
            stmt = stmt.where(Listing.floor <= params.floor_max)
        if params.listing_type is not None:
            stmt = stmt.where(Listing.apartment_type == params.listing_type)
        if params.available_by is not None:
            stmt = stmt.where(Listing.available_from <= params.available_by)

        # Amenities — tri-state
        if params.wbs_required is not None:
            stmt = stmt.where(Listing.wbs_required == params.wbs_required)
        if params.is_furnished is not None:
            stmt = stmt.where(Listing.is_furnished == params.is_furnished)
        if params.has_balcony is not None:
            stmt = stmt.where(Listing.has_balcony == params.has_balcony)
        if params.has_kitchen is not None:
            stmt = stmt.where(Listing.has_kitchen == params.has_kitchen)
        if params.has_elevator is not None:
            stmt = stmt.where(Listing.has_elevator == params.has_elevator)

        # District substring match — OR across multiple districts AND across
        # the three district sources: the scraped `Listing.district` (the
        # pin-less freetext fallback) plus the ALKIS-polygon assignments
        # `listing_bezirk` / `listing_ortsteil` on the (already outer-joined)
        # gold row. "in Tiergarten" then matches whether the source labelled
        # it Mitte, the Ortsteil polygon says Tiergarten, or both.
        if params.districts:
            lgc = ListingGeoContext
            district_clauses = []
            for d in params.districts:
                pattern = _escape_for_substring(d)
                district_clauses.append(Listing.district.ilike(pattern, escape="\\"))
                district_clauses.append(lgc.listing_bezirk.ilike(pattern, escape="\\"))
                district_clauses.append(
                    lgc.listing_ortsteil.ilike(pattern, escape="\\")
                )
            stmt = stmt.where(or_(*district_clauses))

        # Images present
        if params.has_images is True:
            stmt = stmt.where(func.jsonb_array_length(Listing.images) > 0)

        # Proximity to a point — the one flat spatial predicate (raw coords).
        # Hits the functional GiST index on (location::geography).
        if params.near_lat is not None and params.near_lon is not None:
            point = geo_func.ST_SetSRID(
                geo_func.ST_MakePoint(params.near_lon, params.near_lat), 4326
            )
            radius_m = params.radius_km * 1000
            stmt = stmt.where(
                geo_func.ST_DWithin(
                    cast(Listing.location, Geography),
                    cast(point, Geography),
                    radius_m,
                    type_=Boolean,
                )
            )

        # Proximity to a NAMED place — geometry-precise ST_DWithin against the
        # one shape `locate_place` resolved (correct for the Spree LINE and the
        # TU-campus POLYGON; a centroid radius would be wrong for both). The
        # token is the opaque `kind:src_id` `place_ref` from `world.named_places`;
        # we parse only its FORMAT here (split on the FIRST ':') — no table
        # knowledge. A malformed/unknown token yields an empty subquery → no
        # match, never a 500.
        if params.near_place_ref is not None:
            parsed = _parse_place_ref(params.near_place_ref)
            if parsed is not None:
                kind, src_id = parsed
                radius_m = params.radius_km * 1000
                # Scalar subquery: the resolved geometry for this place_ref via
                # the mapped `world.named_places` view. `kind` is constant so
                # Postgres prunes the view's UNION to the one branch; `src_id`
                # hits that base table's PK. Bound params only.
                np = named_places.c
                geom_subq = (
                    select(np.geom)
                    .where(np.kind == kind, np.src_id == src_id)
                    .scalar_subquery()
                )
                # `type_=Boolean` (the SQLAlchemy TypeEngine, NOT the Python
                # `bool` builtin — that one breaks cache-key traversal) so this
                # predicate is cacheable, matching the near_lat/near_lon
                # ST_DWithin above (see the proximity-caching fix on main).
                stmt = stmt.where(
                    geo_func.ST_DWithin(
                        cast(Listing.location, Geography),
                        cast(geom_subq, Geography),
                        radius_m,
                        type_=Boolean,
                    )
                )

        return stmt

    def _apply_geo_context_filters(self, stmt: Select, params: SearchParams) -> Select:
        """Geo-context filters.

        Two shapes:
          - **POI filters** (transit, schools, hospitals, kita, near_park,
            near_playground, near_water): EXISTS against the per-listing
            junction table populated by `gold.enrich_nearby_*`. Honours
            any per-family attribute filter (modes, lines, school_type,
            hospital tier, ...). See
            `agent-compound-docs/decisions/spatial-neighbor-tables.md`.
          - **Scalar / field filters** (inside_ring, max_noise,
            min_greenery, density): B-tree / JSONB-extract predicates on the
            denormalised columns of `listings_geo_context`.
        """
        lgc = ListingGeoContext

        # ----- POI filters via junction tables -----

        if params.transit is not None:
            stmt = self._apply_transit_filter(stmt, params.transit)

        if params.school is not None:
            stmt = self._apply_school_filter(stmt, params.school)

        if params.hospital is not None:
            stmt = self._apply_hospital_filter(stmt, params.hospital)

        if params.kita is not None:
            # Kitas carry no sub-type — pure proximity, same shape as
            # near_park / near_playground / near_water.
            stmt = self._apply_proximity_filter(
                stmt, params.kita.distance, ListingNearbyKita
            )

        if params.near_park is not None:
            # Cemeteries are excluded from listings_nearby_parks at ETL time.
            stmt = self._apply_proximity_filter(
                stmt, params.near_park, ListingNearbyPark
            )

        if params.near_playground is not None:
            stmt = self._apply_proximity_filter(
                stmt, params.near_playground, ListingNearbyPlayground
            )

        if params.near_water is not None:
            stmt = self._apply_proximity_filter(
                stmt, params.near_water, ListingNearbyWater
            )

        # ----- Scalar / field filters on listings_geo_context -----

        # Inside the ring (Umweltzone / S-Bahn ring). Strict equality so it
        # handles both True (inside) and False (outside); NULL rows (gold
        # hasn't assigned a ring flag) drop out under `== False`, which is
        # the desired behaviour — we don't claim a listing is outside the
        # ring when we never tested it.
        if params.inside_ring is not None:
            stmt = stmt.where(lgc.inside_ring == params.inside_ring)

        # Noise — optimistic-include on NULL (no trusted reading within
        # the 50 m gate set in gold.enrich_noise; we don't claim a listing
        # is loud when we have no nearby sample). "noisy" is the absolute
        # max — no filter.
        if params.max_noise is not None:
            if params.max_noise == "quiet":
                cutoff = NOISE_QUIET_MAX_LDEN
            elif params.max_noise == "lively":
                cutoff = NOISE_LIVELY_MAX_LDEN
            else:
                cutoff = None
            if cutoff is not None:
                stmt = stmt.where(
                    or_(lgc.noise_total_lden.is_(None), lgc.noise_total_lden < cutoff)
                )

        # Greenery — composite m² lives inside greenery_profile JSONB.
        if params.min_greenery is not None:
            if params.min_greenery == "leafy":
                stmt = stmt.where(
                    lgc.greenery_profile["green_m2_within_300m"].as_float()
                    >= GREENERY_LEAFY_MIN_M2
                )
            elif params.min_greenery == "very_leafy":
                from flat_chat.listings.thresholds import GREENERY_VERY_LEAFY_MIN_M2

                stmt = stmt.where(
                    lgc.greenery_profile["green_m2_within_300m"].as_float()
                    >= GREENERY_VERY_LEAFY_MIN_M2
                )

        # Density — exact bucket on persons_per_hectare scalar
        if params.density is not None:
            if params.density == "sparse":
                stmt = stmt.where(lgc.persons_per_hectare < DENSITY_SPARSE_MAX)
            elif params.density == "moderate":
                stmt = stmt.where(
                    lgc.persons_per_hectare >= DENSITY_SPARSE_MAX,
                    lgc.persons_per_hectare < DENSITY_MODERATE_MAX,
                )
            elif params.density == "dense":
                stmt = stmt.where(lgc.persons_per_hectare >= DENSITY_MODERATE_MAX)

        return stmt

    def _apply_transit_filter(self, stmt: Select, f: TransitFilter) -> Select:
        """EXISTS-any against `listings_nearby_transit`.

        Restores old-correct "any stop within X m" semantics (the v1 gold
        chip-only shape narrowed this to the nearest stop only). The
        junction table makes `modes` / `lines` / `stop_name` attribute
        filters indexable: `(listing_id, distance_m)` for the range scan,
        GIN on `modes` and `lines`.
        """
        nbr = ListingNearbyTransit
        max_m = resolve_near_spec(f.distance)
        subq = select(nbr.listing_id).where(
            nbr.listing_id == Listing.id,
            nbr.distance_m <= max_m,
        )
        if f.modes:
            mode_codes = encode_modes(list(f.modes))
            subq = subq.where(nbr.modes.op("&&")(cast(mode_codes, ARRAY(Integer))))
        if f.lines:
            subq = subq.where(nbr.lines.op("&&")(cast(f.lines, ARRAY(Text))))
        if f.stop_name:
            pattern = _escape_for_substring(f.stop_name)
            subq = subq.where(nbr.name.ilike(pattern, escape="\\"))
        return stmt.where(subq.exists())

    def _apply_school_filter(self, stmt: Select, f: SchoolFilter) -> Select:
        """EXISTS-any against `listings_nearby_schools` + optional catchment.

        Two intents combine with AND:
          - Proximity: any school within `f.distance`, optionally filtered
            by `school_type` substring (Grundschule / Gymnasium / ...).
          - Legal attendance: `f.requires_catchment=True` requires
            `listings_geo_context.school_catchment` to be non-null.
        """
        nbr = ListingNearbySchool
        max_m = resolve_near_spec(f.distance)
        subq = select(nbr.listing_id).where(
            nbr.listing_id == Listing.id,
            nbr.distance_m <= max_m,
        )
        if f.school_type:
            pattern = _escape_for_substring(f.school_type)
            subq = subq.where(nbr.school_type.ilike(pattern, escape="\\"))
        stmt = stmt.where(subq.exists())
        if f.requires_catchment:
            stmt = stmt.where(ListingGeoContext.school_catchment.is_not(None))
        return stmt

    def _apply_hospital_filter(self, stmt: Select, f: HospitalFilter) -> Select:
        """EXISTS-any against `listings_nearby_hospitals`, optionally tier-filtered.

        `tier="plan_hospital"` (default) restricts to the Krankenhausplan
        network — emergency-care reachable, the usual intent of "near a
        hospital". `tier="any"` widens to specialty clinics too.
        """
        nbr = ListingNearbyHospital
        max_m = resolve_near_spec(f.distance)
        subq = select(nbr.listing_id).where(
            nbr.listing_id == Listing.id,
            nbr.distance_m <= max_m,
        )
        if f.tier == "plan_hospital":
            subq = subq.where(nbr.tier == "plan_hospital")
        # f.tier == "any" → no tier predicate
        return stmt.where(subq.exists())

    def _apply_proximity_filter(self, stmt: Select, spec, model) -> Select:
        """EXISTS-any against a single-attribute proximity junction table.

        Shared by near_park / near_playground / near_water — these tables
        carry no per-family attribute logic, only `(listing_id, distance_m)`.
        """
        max_m = resolve_near_spec(spec)
        subq = select(model.listing_id).where(
            model.listing_id == Listing.id,
            model.distance_m <= max_m,
        )
        return stmt.where(subq.exists())

    async def _embed(self, query: str) -> list[float]:
        """Compute the query embedding (provider-agnostic).

        `input_type="query"` is REQUIRED by `Embedder.embed` (keyword-only) and
        also drives `JinaTaskEmbedder` to pick the asymmetric `retrieval.query`
        LoRA — documents are embedded with `retrieval.passage`. Omitting it
        raises `TypeError` at call time, which aborts the whole agent run.
        """
        if self.embedder is None:  # pragma: no cover - guarded by caller
            raise RuntimeError("embedder required for semantic ranking")
        vectors = await self.embedder.embed([query], input_type="query")
        return list(vectors[0])
