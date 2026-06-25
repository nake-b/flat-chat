"""SearchService — async filter+rank against the gold join.

Post-refactor shape:
  - Joins `listings ⨝ listings_geo_context ⨝ listings_embeddings` and
    filters everything with plain B-tree predicates on the gold scalars
    (no LATERAL chips, no EXISTS-with-ST_DWithin, no per-row spatial work).
  - Returns `list[UiApartment]` — typed Pydantic, not pandas DataFrame.
    The chat layer treats the list as the agent's working memory and
    pushes it to the frontend as `SessionState.results`.
  - The only spatial predicate that survives is `ST_DWithin` on the
    listing's own location for `near_lat/near_lon` proximity search —
    that's a single condition on the listing's location, hits the
    functional GiST index, and is cheap.

The 5 always-on LATERAL chips and 12-query detail fan-out are gone.
Those are now precomputed at gold-ETL time (see
`services/ingestion/src/gold/`). Detail fetches go through
`ListingService.get(id)` via either the agent tool `open_listing` or
the HTTP route `GET /api/listings/{id}`.
"""

from __future__ import annotations

import json
import logging
import re

from geoalchemy2 import Geography
from geoalchemy2 import functions as geo_func
from pgvector.sqlalchemy import Vector
from pydantic_ai import Embedder
from sqlalchemy import ARRAY, Select, Text, case, cast, column, exists, func, or_, select, table
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH
from sqlalchemy.ext.asyncio import AsyncSession

from flat_chat.listings.context import UiApartment
from flat_chat.listings.labels import (
    bucket_density,
    bucket_noise,
    encode_modes,
    resolve_near_spec,
    walk_minutes,
)
from flat_chat.listings.models import Listing, ListingEmbedding, ListingGeoContext
from flat_chat.listings.thresholds import (
    DENSITY_MODERATE_MAX,
    DENSITY_SPARSE_MAX,
    GREENERY_LEAFY_MIN_M2,
    NOISE_LIVELY_MAX_LDEN,
    NOISE_QUIET_MAX_LDEN,
)

from .geo_filters import (
    LandmarkFilter,
    NamedGeoContextFilter,
    TransitFilter,
)
from .schemas import SearchParams

logger = logging.getLogger(__name__)


# SQL LIKE patterns containing `%` or `_` need escaping when we want a
# literal substring match. Mirrors the helper in the old service.
_LIKE_META = re.compile(r"([%_\\])")
_TOKEN_SPLIT = re.compile(r"[\W_]+", flags=re.UNICODE)
_LANDMARK_STOPWORDS = {
    "the",
    "der",
    "die",
    "das",
    "den",
    "dem",
    "des",
    "zu",
    "zur",
    "zum",
    "in",
    "im",
    "am",
    "an",
    "von",
    "of",
}
_LANDMARK_ABBREV_EXPANSIONS: dict[str, list[str]] = {
    # Common shorthand used by users for Humboldt-Universitaet.
    "hu": ["humboldt", "universitaet"],
}


def _escape_for_substring(s: str) -> str:
    return f"%{_LIKE_META.sub(r'\\\1', s)}%"


def _jsonpath_regex(s: str) -> str:
    escaped = re.escape(s)
    return json.dumps(f".*{escaped}.*")


def _jsonpath_like_distance_filter(*, name: str, max_m: int, name_key: str = "name") -> str:
    return (
        f'$[*] ? (@.{name_key} like_regex {_jsonpath_regex(name)} '
        f'flag "i" && @.distance_m <= {max_m})'
    )


_BUILDINGS = table("buildings", column("name"), column("geom"))


def _landmark_name_tokens(name: str) -> list[str]:
    raw_tokens = [part.lower() for part in _TOKEN_SPLIT.split(name) if part]
    expanded_tokens: list[str] = []
    for token in raw_tokens:
        expanded_tokens.append(token)
        expanded_tokens.extend(_LANDMARK_ABBREV_EXPANSIONS.get(token, []))

    filtered = [
        token
        for token in expanded_tokens
        if token not in _LANDMARK_STOPWORDS and (len(token) >= 3 or (len(token) == 2 and token.isalpha()))
    ]
    seen: set[str] = set()
    deduped: list[str] = []
    for token in filtered:
        if token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return deduped


def _token_variants(token: str) -> set[str]:
    variants = {token}
    variants.add(
        token.replace("ae", "ä").replace("oe", "ö").replace("ue", "ü").replace("ss", "ß")
    )
    variants.add(
        token.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    )
    return {variant for variant in variants if variant}


def _landmark_name_predicate(name: str):
    full_pattern = _escape_for_substring(name)
    full_phrase = _BUILDINGS.c.name.ilike(full_pattern, escape="\\")

    tokens = _landmark_name_tokens(name)
    if not tokens:
        return full_phrase

    token_matches = []
    for token in tokens:
        if len(token) <= 2:
            # Short acronyms (e.g. "HU") should match as standalone words,
            # not as arbitrary substrings inside other words.
            regex = rf"(^|[^[:alnum:]]){re.escape(token)}([^[:alnum:]]|$)"
            token_clause = _BUILDINGS.c.name.op("~*")(regex)
        else:
            variant_clauses = [
                _BUILDINGS.c.name.ilike(_escape_for_substring(variant), escape="\\")
                for variant in _token_variants(token)
            ]
            token_clause = or_(*variant_clauses)
        token_matches.append(case((token_clause, 1), else_=0))
    required = len(tokens) if len(tokens) <= 2 else 2
    token_threshold = sum(token_matches) >= required
    return or_(full_phrase, token_threshold)


def _landmark_within_exists(*, name: str, max_m: int):
    """True radius search: listing within `max_m` meters of a named ALKIS building footprint."""
    subq = (
        select(1)
        .select_from(_BUILDINGS)
        .where(
            _BUILDINGS.c.name.is_not(None),
            _BUILDINGS.c.geom.is_not(None),
            _landmark_name_predicate(name),
            geo_func.ST_DWithin(
                cast(Listing.location, Geography),
                cast(_BUILDINGS.c.geom, Geography),
                max_m,
            ),
        )
        .correlate(Listing)
    )
    return exists(subq)


class SearchService:
    """Filter + rank listings. Returns the tier-2 cards. Agent-only consumer.

    HTTP routes don't call this; `ListingService` handles direct reads.
    """

    def __init__(self, db: AsyncSession, embedder: Embedder | None = None):
        self.db = db
        self.embedder = embedder

    async def search(self, params: SearchParams) -> tuple[list[UiApartment], int]:
        """Execute the search. Returns (results, total_count_hint).

        `total_count_hint` is the number of rows the query yielded before
        applying LIMIT — useful for "Found N listings, showing top X"
        prose. Currently equal to len(results) when results < limit;
        beyond that we'd need a separate COUNT(*) which is overkill.
        """
        stmt = await self._build_statement(params)

        logger.info(
            "Searching: %s",
            params.model_dump(exclude_defaults=True, exclude_none=True),
        )
        result = await self.db.execute(stmt)
        rows = result.all()
        logger.info("Found %d results", len(rows))

        cards = [_row_to_uiapartment(row, with_score=bool(params.query)) for row in rows]
        return cards, len(cards)

    # ---- Statement composition ----

    async def _build_statement(self, params: SearchParams) -> Select:
        """Compose the SELECT against `listings ⨝ listings_geo_context (⨝ embeddings)`.

        Plain joins; every geo-context filter is a B-tree predicate on
        the gold columns. The only spatial work is the optional
        `near_lat/near_lon` radius filter, which uses the functional GiST
        index on `listings.location::geography`.
        """
        stmt: Select = (
            select(
                Listing,
                ListingGeoContext.nearest_transit_lines,
                ListingGeoContext.nearest_transit_m,
                ListingGeoContext.nearest_transit_name,
                ListingGeoContext.nearest_park_name,
                ListingGeoContext.nearest_park_m,
                ListingGeoContext.noise_total_lden,
                ListingGeoContext.persons_per_hectare,
            )
            .outerjoin(
                ListingGeoContext, ListingGeoContext.listing_id == Listing.id
            )
        )

        stmt = self._apply_listing_filters(stmt, params)
        stmt = self._apply_geo_context_filters(stmt, params)

        # Semantic ranking via embedding cosine distance — only when the
        # user provided a query. Otherwise sort_by=relevance degrades to
        # recency (still useful; just doesn't reflect query terms).
        sort_effective = params.sort_by
        distance = None
        if params.query and self.embedder:
            embedding = await self._embed(params.query)
            stmt = stmt.outerjoin(
                ListingEmbedding, ListingEmbedding.listing_id == Listing.id
            )
            distance = ListingEmbedding.embedding.cosine_distance(
                cast(embedding, Vector(1024))
            )
            stmt = stmt.add_columns(distance.label("similarity_score"))
        elif params.query and not self.embedder:
            logger.warning(
                "sort_by=relevance with query but no embedder — "
                "falling back to recent"
            )
            sort_effective = "recent"
        elif params.sort_by == "relevance" and not params.query:
            logger.info(
                "sort_by=relevance with no query — falling back to recent"
            )
            sort_effective = "recent"

        if sort_effective == "relevance" and distance is not None:
            stmt = stmt.order_by(distance)
        elif sort_effective == "price":
            stmt = stmt.order_by(Listing.warm_rent_eur.asc().nulls_last())
        elif sort_effective == "area":
            stmt = stmt.order_by(Listing.area_sqm.desc().nulls_last())
        else:
            stmt = stmt.order_by(Listing.ingested_at.desc())

        return stmt.limit(params.limit)

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

        # District substring match — OR across multiple districts.
        if params.districts:
            district_clauses = []
            for district in params.districts:
                pattern = _escape_for_substring(district)
                district_clauses.append(
                    or_(
                        Listing.district.ilike(pattern, escape="\\"),
                        ListingGeoContext.listing_bezirk.ilike(pattern, escape="\\"),
                        ListingGeoContext.listing_ortsteil.ilike(pattern, escape="\\"),
                    )
                )
            stmt = stmt.where(or_(*district_clauses))

        # Images present
        if params.has_images is True:
            stmt = stmt.where(func.jsonb_array_length(Listing.images) > 0)

        # Proximity to a point — the one spatial predicate that survives.
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
                    type_=bool,
                )
            )

        return stmt

    def _apply_geo_context_filters(
        self, stmt: Select, params: SearchParams
    ) -> Select:
        """Filters that read off `listings_geo_context` (gold).

        All B-tree predicates against pre-computed scalars. None of the
        old LATERAL/EXISTS spatial subqueries — those work happens once
        at gold-ETL time.
        """
        lgc = ListingGeoContext

        # Transit — distance threshold + line/mode/name predicates
        if params.transit is not None:
            stmt = self._apply_transit_filter(stmt, params.transit)

        # School filter — listings inside any catchment (school_catchment
        # JSONB non-null). Distance/type are kept in spec for now but the
        # post-refactor implementation only checks catchment membership.
        if params.school is not None:
            stmt = stmt.where(lgc.school_catchment.is_not(None))

        # Hospital filter — gold's hospitals_top2 is non-null if any
        # hospital was within the cap distance.
        if params.hospital is not None:
            stmt = stmt.where(lgc.hospitals_top2.is_not(None))

        # Near-park / near-playground / near-water filters — distance cutoff
        if params.near_park is not None:
            stmt = stmt.where(
                lgc.nearest_park_m.is_not(None),
                lgc.nearest_park_m <= resolve_near_spec(params.near_park),
            )
        if params.near_playground is not None:
            radius = resolve_near_spec(params.near_playground)
            # Playground JSONB stores distance_m; cast and compare. We
            # could also pull this onto a dedicated indexed column if
            # this filter sees real use.
            stmt = stmt.where(
                lgc.playground["distance_m"].as_integer() <= radius
            )
        if params.near_water is not None:
            radius = resolve_near_spec(params.near_water)
            stmt = stmt.where(lgc.water["distance_m"].as_integer() <= radius)

        # Toilets — require nearest_toilet_m to be within default cap
        if getattr(params, "has_nearby_toilet", None) is not None:
            if params.has_nearby_toilet:
                # any toilet within 1500m (same cap as transit by default)
                stmt = stmt.where(lgc.nearest_toilet_m.is_not(None), lgc.nearest_toilet_m <= 1500)
            else:
                stmt = stmt.where(lgc.nearest_toilet_m.is_(None) | (lgc.nearest_toilet_m > 1500))

        # Trees — require at least one tree within 100m
        if getattr(params, "has_tree_within_100m", None) is not None:
            if params.has_tree_within_100m:
                stmt = stmt.where(
                    lgc.trees_within_100_count.is_not(None),
                    lgc.trees_within_100_count >= 1,
                )
            else:
                stmt = stmt.where(
                    (lgc.trees_within_100_count.is_(None))
                    | (lgc.trees_within_100_count == 0)
                )

        # Noise — user picks max bucket; convert to dB threshold.
        if params.max_noise is not None:
            if params.max_noise == "quiet":
                stmt = stmt.where(lgc.noise_total_lden < NOISE_QUIET_MAX_LDEN)
            elif params.max_noise == "lively":
                stmt = stmt.where(lgc.noise_total_lden < NOISE_LIVELY_MAX_LDEN)
            # "noisy" is the absolute max — no filter

        # Greenery — minimum bucket
        if params.min_greenery is not None:
            # Gold stores green_m2_within_300m in the JSONB blob; the
            # buckets sit at 5000 (leafy) and 10000 (very_leafy).
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

        # Landmark filter — named ALKIS building/POI proximity.
        if getattr(params, "landmark", None) is not None:
            max_m = resolve_near_spec(params.landmark.distance)
            # Preserve the post-refactor invariant: any geo predicate must exclude
            # listings without a gold row (otherwise cards render without chips).
            stmt = stmt.where(
                lgc.listing_id.is_not(None),
                _landmark_within_exists(name=params.landmark.name, max_m=max_m),
            )

        # Generic name-based "near X" filters (schools, parks, water, …).
        # AND semantics across the list: every named_geo constraint must match.
        if getattr(params, "named_geo", None):
            for filt in params.named_geo or []:
                stmt = self._apply_named_geo_filter(stmt, filt)

        # Density — exact bucket
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

    def _apply_named_geo_filter(self, stmt: Select, filt: NamedGeoContextFilter) -> Select:
        lgc = ListingGeoContext
        max_m = resolve_near_spec(filt.distance)

        if filt.kind == "landmark":
            stmt = stmt.where(lgc.listing_id.is_not(None))
            return stmt.where(_landmark_within_exists(name=filt.name, max_m=max_m))

        if filt.kind == "school":
            path = _jsonpath_like_distance_filter(name=filt.name, max_m=max_m, name_key="name")
            return stmt.where(
                lgc.schools_top3.is_not(None),
                func.jsonb_path_exists(lgc.schools_top3, cast(path, JSONPATH)),
            )

        if filt.kind == "kita":
            path = _jsonpath_like_distance_filter(name=filt.name, max_m=max_m, name_key="name")
            return stmt.where(
                lgc.kitas_top3.is_not(None),
                func.jsonb_path_exists(lgc.kitas_top3, cast(path, JSONPATH)),
            )

        if filt.kind == "park":
            path = _jsonpath_like_distance_filter(name=filt.name, max_m=max_m, name_key="name")
            return stmt.where(
                lgc.parks_top2.is_not(None),
                func.jsonb_path_exists(lgc.parks_top2, cast(path, JSONPATH)),
            )

        if filt.kind == "hospital":
            path = _jsonpath_like_distance_filter(name=filt.name, max_m=max_m, name_key="name")
            return stmt.where(
                lgc.hospitals_top2.is_not(None),
                func.jsonb_path_exists(lgc.hospitals_top2, cast(path, JSONPATH)),
            )

        if filt.kind == "transit_stop":
            path = _jsonpath_like_distance_filter(name=filt.name, max_m=max_m, name_key="name")
            return stmt.where(
                lgc.transit_top3.is_not(None),
                func.jsonb_path_exists(lgc.transit_top3, cast(path, JSONPATH)),
            )

        if filt.kind == "playground":
            return stmt.where(
                lgc.playground.is_not(None),
                lgc.playground["name"].as_string().ilike(_escape_for_substring(filt.name)),
                lgc.playground["distance_m"].as_integer() <= max_m,
            )

        if filt.kind == "water":
            return stmt.where(
                lgc.water.is_not(None),
                lgc.water["name"].as_string().ilike(_escape_for_substring(filt.name)),
                lgc.water["distance_m"].as_integer() <= max_m,
            )

        raise ValueError(f"unsupported named_geo kind: {filt.kind!r}")

    def _apply_transit_filter(
        self, stmt: Select, f: TransitFilter
    ) -> Select:
        """Filter on the nearest transit stop chip + optional line/mode/name.

        Distance bucket → B-tree on `nearest_transit_m`. Line filter
        uses GIN-indexed array overlap. Mode and stop_name go through
        the JSONB top-3 blob since they're not on the chip columns.
        """
        lgc = ListingGeoContext
        max_m = resolve_near_spec(f.distance)
        stmt = stmt.where(
            lgc.nearest_transit_m.is_not(None),
            lgc.nearest_transit_m <= max_m,
        )
        if f.lines:
            # Array overlap — hits the GIN index on nearest_transit_lines.
            # Explicit ARRAY(Text) cast on the bound param: asyncpg
            # otherwise binds list[str] as varchar[] and Postgres has no
            # `text[] && varchar[]` operator.
            stmt = stmt.where(
                lgc.nearest_transit_lines.op("&&")(cast(f.lines, ARRAY(Text)))
            )
        if f.modes:
            # Mode codes live as int arrays inside each element of the
            # top-3 JSONB. We test "ANY of the top-3 stops includes ANY of
            # the requested modes" because filtering on just `[0]` (the
            # nearest stop) loses ~4× the matches — most kleinanzeigen
            # listings sit closer to a bus stop than a U-Bahn, but U-Bahn
            # is in the top-3 within a reasonable walk.
            #
            # SQL shape: `transit_top3 @> '[{"modes":[400]}]'::jsonb` —
            # jsonb-contains over an array of objects matches if ANY
            # element of the left contains the structure of the right.
            # OR'd across requested mode codes.
            #
            # `nearest_transit_m <= distance` (already applied above)
            # still gates on the nearest stop's distance, so the overall
            # filter reads as "any transit within X meters AND U-Bahn
            # somewhere in top 3 nearest stops". A dedicated per-mode
            # nearest-distance column is the eventual win; out of scope
            # for now.
            mode_codes = encode_modes(list(f.modes))
            mode_predicates = [
                lgc.transit_top3.op("@>")(
                    cast([{"modes": [code]}], JSONB)
                )
                for code in mode_codes
            ]
            stmt = stmt.where(or_(*mode_predicates))
        if f.stop_name:
            pattern = _escape_for_substring(f.stop_name)
            stmt = stmt.where(lgc.nearest_transit_name.ilike(pattern, escape="\\"))
        return stmt

    async def _embed(self, query: str) -> list[float]:
        """Compute the query embedding (provider-agnostic)."""
        if self.embedder is None:  # pragma: no cover - guarded by caller
            raise RuntimeError("embedder required for semantic ranking")
        vectors = await self.embedder.embed([query])
        return vectors[0]


# ---------------------------------------------------------------------------
# Row → UiApartment projection. Labels applied here via listings.labels —
# gold stores raw values; this is the point where presentation meets data.
# ---------------------------------------------------------------------------


def _row_to_uiapartment(row, *, with_score: bool) -> UiApartment:
    """Build a UiApartment from a SELECT row.

    The row is the tuple shape `_build_statement` selects:
        (Listing, nearest_transit_lines, nearest_transit_m,
         nearest_transit_name, nearest_park_name, nearest_park_m,
         noise_total_lden, persons_per_hectare,
         [similarity_score])
    """
    listing: Listing = row[0]
    mapping = row._mapping

    nearest_transit_lines = mapping.get("nearest_transit_lines")
    nearest_transit_line = (
        nearest_transit_lines[0] if nearest_transit_lines else None
    )
    nearest_transit_m = mapping.get("nearest_transit_m")
    noise_lden = mapping.get("noise_total_lden")
    pph = mapping.get("persons_per_hectare")

    # Pick the first image URL if any (browser handles the rest via HTTP
    # detail fetch; the card just needs a thumbnail).
    image_url: str | None = None
    if listing.images:
        for item in listing.images:
            if isinstance(item, str):
                image_url = item
                break
            if isinstance(item, dict) and isinstance(item.get("url"), str):
                image_url = item["url"]
                break

    sim_score = None
    if with_score and "similarity_score" in mapping:
        # Postgres cosine_distance returns 0..2; convert to 0..1 similarity
        raw = mapping["similarity_score"]
        if raw is not None:
            sim_score = round(1 - float(raw), 4)

    return UiApartment(
        id=str(listing.id),
        lat=listing.latitude,
        lng=listing.longitude,
        price_warm_eur=listing.warm_rent_eur,
        price_cold_eur=listing.cold_rent_eur,
        nebenkosten_eur=listing.nebenkosten_eur,
        kaution_eur=listing.kaution_eur,
        rooms=listing.rooms,
        bedrooms=listing.bedrooms,
        area_sqm=listing.area_sqm,
        floor=listing.floor,
        floors_total=listing.floors_total,
        available_from=(
            listing.available_from.isoformat() if listing.available_from else None
        ),
        listing_type=listing.apartment_type,
        district=listing.district,
        title=listing.title,
        address=listing.address,
        wbs_required=listing.wbs_required,
        is_furnished=listing.is_furnished,
        has_balcony=listing.has_balcony,
        has_kitchen=listing.has_kitchen,
        has_elevator=listing.has_elevator,
        has_garden=listing.has_garden,
        heating=listing.heating,
        energy_consumption_kwh=listing.energy_consumption_kwh,
        lister_type=listing.lister_type,
        source_url=listing.listing_url,
        image_url=image_url,
        nearest_transit_line=nearest_transit_line,
        walk_min_to_transit=walk_minutes(nearest_transit_m),
        nearest_park_name=mapping.get("nearest_park_name"),
        nearest_park_m=mapping.get("nearest_park_m"),
        noise_label=bucket_noise(noise_lden),
        density_label=bucket_density(pph),
        similarity_score=sim_score,
    )
