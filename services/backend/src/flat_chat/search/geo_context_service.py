"""GeoContextService — internal seam owning all 14 geo-context silver tables.

`SearchService` is the agent-facing facade. This service is *internal* — it
composes underneath SearchService and is never exposed through the tool
surface. Three responsibilities:

1. **Pre-filter** (`apply_filters`) — given a SearchParams with geo filters
   set, augment a SQLAlchemy `Select` with the right `EXISTS ST_DWithin`
   / `ST_Contains` predicates so the result set narrows.
2. **Per-card chips** (`apply_chips`) — augment a `Select` with LATERAL
   joins that produce small enrichment columns (nearest transit line +
   walk-min, noise label, density label, MSS labels, nearest park).
   These run on every search whether or not the user asked for them;
   the indexes make them cheap.
3. **Per-listing context** (`context_for`) — fat enrichment for one
   listing's detail panel; runs the per-dataset sub-queries and returns
   a typed `ListingContext` blob.

Numeric thresholds and label vocab live in `distances.py`, `buckets.py`,
and `transit.py` — see those modules and the threshold doc.
"""

from geoalchemy2 import Geography, WKBElement
from geoalchemy2 import functions as geo_func
from sqlalchemy import Float, Integer, Select, and_, cast, func, select, true
from sqlalchemy.orm import Session

from .buckets import (
    DENSITY_MODERATE_MAX,
    DENSITY_SPARSE_MAX,
    NOISE_LIVELY_MAX_LDEN,
    NOISE_QUIET_MAX_LDEN,
    bucket_density,
    bucket_greenery,
    bucket_noise,
)
from .distances import (
    CAP_HOSPITALS_M,
    CAP_PARKS_M,
    CAP_PLAYGROUNDS_M,
    CAP_SCHOOLS_M,
    CAP_TRANSIT_STOPS_M,
    CAP_WATER_M,
    resolve_near_spec,
    walk_minutes,
)
from .geo_filters import (
    DensityProfile,
    GreeneryProfile,
    HospitalFilter,
    ListingContext,
    MssDynamics,
    MssFilter,
    MssProfile,
    MssStatus,
    NearestHospital,
    NearestPark,
    NearestPlayground,
    NearestSchool,
    NearestTransitStop,
    NearestWater,
    NoiseProfile,
    SchoolCatchmentInfo,
    SchoolFilter,
    TransitFilter,
)
from .geo_models import (
    DisabledParking,
    Hospital,
    Park,
    Playground,
    PopulationDensity2025,
    School,
    SchoolCatchment,
    SocialMonitoring2025,
    StreetNoise2022,
    TransitStop,
    WaterBody,
)
from .models import Listing
from .schemas import SearchParams
from .transit import decode_modes, resolve_modes

# ---------------------------------------------------------------------------
# MSS English ↔ German label maps. Source: threshold doc §8. These are used
# in the MSS filter, the MSS chip, and the MSS profile so the German labels
# the data publishes never leak out of this service.
# ---------------------------------------------------------------------------

MSS_STATUS_EN_TO_DE: dict[MssStatus, str] = {
    "disadvantaged": "sehr niedrig",
    "lower-income": "niedrig",
    "mixed": "mittel",
    "affluent": "hoch",
}
MSS_STATUS_DE_TO_EN: dict[str, MssStatus] = {
    v: k for k, v in MSS_STATUS_EN_TO_DE.items()
}

# Numeric rank used by `status_min` floor — higher == more affluent.
_MSS_STATUS_RANK: dict[MssStatus, int] = {
    "disadvantaged": 0,
    "lower-income": 1,
    "mixed": 2,
    "affluent": 3,
}

MSS_DYNAMICS_EN_TO_DE: dict[MssDynamics, str] = {
    "improving": "positiv",
    "stable": "stabil",
    "slipping": "negativ",
}
MSS_DYNAMICS_DE_TO_EN: dict[str, MssDynamics] = {
    v: k for k, v in MSS_DYNAMICS_EN_TO_DE.items()
}


# ---------------------------------------------------------------------------
# Cemetery exclusion (threshold doc §5). `Friedhof` is the German term in
# `parks.object_type`. Case-insensitive substring match covers
# "Friedhof", "Jüdischer Friedhof", "Alter Friedhof", etc.
# ---------------------------------------------------------------------------

_FRIEDHOF_PATTERN = "%friedhof%"


def _not_cemetery():
    """SQLAlchemy predicate excluding cemetery rows from `parks`.

    Used in 3 places (filter, chip, context) so kept module-level to stay DRY.
    """
    return Park.object_type.notilike(_FRIEDHOF_PATTERN)


class GeoContextService:
    """Owns all geo-context table access. Injected into `SearchService`."""

    def __init__(self, db: Session):
        self.db = db

    # -----------------------------------------------------------------
    # Pre-filter — narrow the listings result set
    # -----------------------------------------------------------------

    def apply_filters(self, stmt: Select, params: SearchParams) -> Select:
        """Add pre-filter predicates to a listings `Select` based on geo args.

        Skips any filter the user didn't set (None values are no-ops).
        Returns the modified statement; doesn't mutate the input.
        """
        if params.transit is not None:
            stmt = self._apply_transit_filter(stmt, params.transit)
        if params.school is not None:
            stmt = self._apply_school_filter(stmt, params.school)
        if params.hospital is not None:
            stmt = self._apply_hospital_filter(stmt, params.hospital)
        if params.mss is not None:
            stmt = self._apply_mss_filter(stmt, params.mss)
        if params.near_park is not None:
            stmt = self._apply_near_park_filter(stmt, params.near_park)
        if params.near_playground is not None:
            stmt = self._apply_near_playground_filter(stmt, params.near_playground)
        if params.near_water is not None:
            stmt = self._apply_near_water_filter(stmt, params.near_water)
        if params.max_noise is not None:
            stmt = self._apply_noise_filter(stmt, params.max_noise)
        if params.min_greenery is not None:
            stmt = self._apply_greenery_filter(stmt, params.min_greenery)
        if params.density is not None:
            stmt = self._apply_density_filter(stmt, params.density)
        return stmt

    # Private per-filter builders — each returns the augmented Select.
    # The body of each method translates the typed filter into an
    # EXISTS ST_DWithin / ST_Contains predicate using the listing's
    # `location` column.

    def _apply_transit_filter(self, stmt: Select, f: TransitFilter) -> Select:
        """Add EXISTS predicate on transit_stops within `f.distance` of listing.

        Combines mode, line, and stop-name filters. Each component is optional
        and gets ANDed in. Mode/line use Postgres array `&&` (overlap).
        """
        max_m = resolve_near_spec(f.distance)
        subq = (
            select(TransitStop.stop_id)
            .where(
                geo_func.ST_DWithin(
                    cast(TransitStop.geom, Geography),
                    cast(Listing.location, Geography),
                    max_m,
                )
            )
            .correlate(Listing)
        )
        if f.modes:
            subq = subq.where(
                TransitStop.modes_served.overlap(resolve_modes(f.modes))
            )
        if f.lines:
            subq = subq.where(TransitStop.lines_served.overlap(f.lines))
        if f.stop_name:
            subq = subq.where(TransitStop.name.ilike(f"%{f.stop_name}%"))
        return stmt.where(subq.exists())

    def _apply_school_filter(self, stmt: Select, f: SchoolFilter) -> Select:
        """EXISTS within `f.distance` of a school. Optionally filter type.

        `school_type` is a free-text substring match against the German
        Schulverzeichnis category — values like "Grundschule",
        "Gymnasium", "ISS" are literal source values, so case-insensitive
        ILIKE works without a controlled vocab.
        """
        max_m = resolve_near_spec(f.distance)
        subq = (
            select(School.id)
            .where(
                geo_func.ST_DWithin(
                    cast(School.geom, Geography),
                    cast(Listing.location, Geography),
                    max_m,
                )
            )
            .correlate(Listing)
        )
        if f.school_type:
            subq = subq.where(School.school_type.ilike(f"%{f.school_type}%"))
        return stmt.where(subq.exists())

    def _apply_hospital_filter(self, stmt: Select, f: HospitalFilter) -> Select:
        """EXISTS within `f.distance` of a hospital, restricted by tier.

        `plan_hospital` (default) = Krankenhausplan network — emergency-care
        reachable; this is what users usually mean by "near a hospital".
        `any` = both tiers (includes specialty clinics).
        """
        max_m = resolve_near_spec(f.distance)
        subq = (
            select(Hospital.id)
            .where(
                geo_func.ST_DWithin(
                    cast(Hospital.geom, Geography),
                    cast(Listing.location, Geography),
                    max_m,
                )
            )
            .correlate(Listing)
        )
        if f.tier == "plan_hospital":
            subq = subq.where(Hospital.tier == "plan_hospital")
        # tier == "any" → no extra filter (matches both plan_hospital + other).
        return stmt.where(subq.exists())

    def _apply_mss_filter(self, stmt: Select, f: MssFilter) -> Select:
        """ST_Contains predicate on the Sozialmonitoring planning area.

        `status_min` maps English → German rank labels via MSS_STATUS_EN_TO_DE
        then expands to an IN (...) of all German labels at-or-above the floor.
        `dynamics` is exact-match (no ordering — "improving" != ≥ stable).
        """
        floor_rank = _MSS_STATUS_RANK[f.status_min]
        allowed_de = [
            MSS_STATUS_EN_TO_DE[en]
            for en, rank in _MSS_STATUS_RANK.items()
            if rank >= floor_rank
        ]
        subq = (
            select(SocialMonitoring2025.id)
            .where(
                geo_func.ST_Contains(
                    SocialMonitoring2025.geom,
                    Listing.location,
                )
            )
            .where(SocialMonitoring2025.status_index_label.in_(allowed_de))
            .correlate(Listing)
        )
        if f.dynamics is not None:
            subq = subq.where(
                SocialMonitoring2025.dynamics_index_label
                == MSS_DYNAMICS_EN_TO_DE[f.dynamics]
            )
        return stmt.where(subq.exists())

    def _apply_near_park_filter(self, stmt: Select, spec) -> Select:
        """EXISTS a non-cemetery park within `spec` of the listing.

        Cemeteries (object_type ILIKE '%friedhof%') are excluded from named
        park results per threshold doc §5 — Berliners are surprised to see
        "🌳 Jüdischer Friedhof" as their nearest park.
        """
        max_m = resolve_near_spec(spec)
        subq = (
            select(Park.id)
            .where(
                geo_func.ST_DWithin(
                    cast(Park.geom, Geography),
                    cast(Listing.location, Geography),
                    max_m,
                )
            )
            .where(_not_cemetery())
            .correlate(Listing)
        )
        return stmt.where(subq.exists())

    def _apply_near_playground_filter(self, stmt: Select, spec) -> Select:
        """EXISTS a playground within `spec` of the listing."""
        max_m = resolve_near_spec(spec)
        subq = (
            select(Playground.id)
            .where(
                geo_func.ST_DWithin(
                    cast(Playground.geom, Geography),
                    cast(Listing.location, Geography),
                    max_m,
                )
            )
            .correlate(Listing)
        )
        return stmt.where(subq.exists())

    def _apply_near_water_filter(self, stmt: Select, spec) -> Select:
        """EXISTS a water body within `spec` of the listing.

        `water_bodies.geom` is generic Geometry (polygon / multipolygon /
        collection per the migration), but ST_DWithin handles any geom
        type uniformly.
        """
        max_m = resolve_near_spec(spec)
        subq = (
            select(WaterBody.id)
            .where(
                geo_func.ST_DWithin(
                    cast(WaterBody.geom, Geography),
                    cast(Listing.location, Geography),
                    max_m,
                )
            )
            .correlate(Listing)
        )
        return stmt.where(subq.exists())

    def _apply_noise_filter(self, stmt: Select, max_noise) -> Select:
        """Nearest noise sample must be below the chosen Lden cutoff.

        `quiet` → noise_total_lden < 55. `lively` → < 65. We don't expose
        `noisy` as a filter — it's the *opposite* of what renters ask for.

        Implementation: a correlated subquery that picks the single
        nearest `street_noise_2022` point (KNN ORDER BY <->) and requires
        its lden to satisfy the cutoff.
        """
        cutoff = (
            NOISE_QUIET_MAX_LDEN if max_noise == "quiet" else NOISE_LIVELY_MAX_LDEN
        )
        subq = (
            select(StreetNoise2022.noise_total_lden)
            .order_by(StreetNoise2022.geom.op("<->")(Listing.location))
            .limit(1)
            .correlate(Listing)
            .scalar_subquery()
        )
        return stmt.where(subq < cutoff)

    def _apply_greenery_filter(self, stmt: Select, min_greenery) -> Select:
        """Filter proxy for greenery — keep cheap, defer the composite.

        The full WHO Europe composite (parks + playgrounds + 0.5 *
        cemeteries area within 300m) is expensive to compute per row, so
        we use a simpler proxy here:
          - leafy: ≥ 1 non-cemetery park within 300m.
          - very_leafy: ≥ 1 non-cemetery park within 150m.

        The composite still runs in `_greenery_profile` for the detail
        panel where we only enrich one listing at a time. Documented in
        threshold doc §4.
        """
        radius_m = 150 if min_greenery == "very_leafy" else 300
        subq = (
            select(Park.id)
            .where(
                geo_func.ST_DWithin(
                    cast(Park.geom, Geography),
                    cast(Listing.location, Geography),
                    radius_m,
                )
            )
            .where(_not_cemetery())
            .correlate(Listing)
        )
        return stmt.where(subq.exists())

    def _apply_density_filter(self, stmt: Select, density) -> Select:
        """ST_Contains predicate on the listing's LOR + bucket compare.

        sparse: population_per_hectare < 50.
        moderate: 50 ≤ population_per_hectare < 150.
        dense: population_per_hectare ≥ 150.
        """
        ppha = PopulationDensity2025.population_per_hectare
        if density == "sparse":
            bucket_pred = ppha < DENSITY_SPARSE_MAX
        elif density == "moderate":
            bucket_pred = and_(
                ppha >= DENSITY_SPARSE_MAX, ppha < DENSITY_MODERATE_MAX
            )
        else:  # dense
            bucket_pred = ppha >= DENSITY_MODERATE_MAX
        subq = (
            select(PopulationDensity2025.id)
            .where(
                geo_func.ST_Contains(
                    PopulationDensity2025.geom,
                    Listing.location,
                )
            )
            .where(bucket_pred)
            .correlate(Listing)
        )
        return stmt.where(subq.exists())

    # -----------------------------------------------------------------
    # Per-card chips — always-on enrichment columns
    # -----------------------------------------------------------------

    def apply_chips(self, stmt: Select) -> Select:
        """Attach LATERAL chip columns to a listings `Select`.

        Chips wired (all OUTER JOIN LATERAL — never drop listings):
          - transit: nearest_transit_line, nearest_transit_m
          - parks: nearest_park_name, nearest_park_m (excludes cemeteries)
          - noise: noise_total_lden (Python derives label via bucket_noise)
          - density: persons_per_hectare (Python derives label)
          - mss: mss_status_de, mss_dynamics_de (Python translates to EN)

        Greenery chip is intentionally skipped — the composite area-sum is
        too expensive per row. Greenery surfaces only in `context_for`.
        Cemeteries are excluded from the named-nearest-park chip per
        threshold doc §5.
        """
        # Each lateral subquery is correlated to `Listing` (it references
        # `Listing.location`). When multiple laterals are stacked via
        # `.outerjoin`, SQLAlchemy can't infer which FROM each lateral
        # should hang off of — so we anchor every outerjoin to `Listing`
        # explicitly via `.join_from(Listing, lateral, true())`.

        # ---- Nearest transit stop ----
        near_transit = (
            select(
                TransitStop.lines_served[1].label("line"),
                geo_func.ST_Distance(
                    cast(TransitStop.geom, Geography),
                    cast(Listing.location, Geography),
                ).label("distance_m"),
            )
            .where(
                geo_func.ST_DWithin(
                    cast(TransitStop.geom, Geography),
                    cast(Listing.location, Geography),
                    CAP_TRANSIT_STOPS_M,
                )
            )
            .order_by(TransitStop.geom.op("<->")(Listing.location))
            .limit(1)
            .lateral("near_transit")
        )
        stmt = stmt.join_from(Listing, near_transit, true(), isouter=True)
        stmt = stmt.add_columns(
            near_transit.c.line.label("nearest_transit_line"),
            cast(near_transit.c.distance_m, Integer).label("nearest_transit_m"),
        )

        # ---- Nearest park (cemeteries excluded) ----
        near_park = (
            select(
                Park.name.label("name"),
                geo_func.ST_Distance(
                    cast(Park.geom, Geography),
                    cast(Listing.location, Geography),
                ).label("distance_m"),
            )
            .where(
                geo_func.ST_DWithin(
                    cast(Park.geom, Geography),
                    cast(Listing.location, Geography),
                    CAP_PARKS_M,
                )
            )
            .where(_not_cemetery())
            .order_by(Park.geom.op("<->")(Listing.location))
            .limit(1)
            .lateral("near_park")
        )
        stmt = stmt.join_from(Listing, near_park, true(), isouter=True)
        stmt = stmt.add_columns(
            near_park.c.name.label("nearest_park_name"),
            cast(near_park.c.distance_m, Integer).label("nearest_park_m"),
        )

        # ---- Nearest noise sample ----
        near_noise = (
            select(
                StreetNoise2022.noise_total_lden.label("lden"),
            )
            .order_by(StreetNoise2022.geom.op("<->")(Listing.location))
            .limit(1)
            .lateral("near_noise")
        )
        stmt = stmt.join_from(Listing, near_noise, true(), isouter=True)
        stmt = stmt.add_columns(
            cast(near_noise.c.lden, Float).label("noise_total_lden"),
        )

        # ---- Density (LOR polygon contains listing) ----
        in_density = (
            select(
                PopulationDensity2025.population_per_hectare.label("ppha"),
            )
            .where(
                geo_func.ST_Contains(
                    PopulationDensity2025.geom,
                    Listing.location,
                )
            )
            .limit(1)
            .lateral("in_density")
        )
        stmt = stmt.join_from(Listing, in_density, true(), isouter=True)
        stmt = stmt.add_columns(
            cast(in_density.c.ppha, Float).label("persons_per_hectare"),
        )

        # ---- MSS (Sozialmonitoring planning area contains listing) ----
        in_mss = (
            select(
                SocialMonitoring2025.status_index_label.label("status_de"),
                SocialMonitoring2025.dynamics_index_label.label("dynamics_de"),
            )
            .where(
                geo_func.ST_Contains(
                    SocialMonitoring2025.geom,
                    Listing.location,
                )
            )
            .limit(1)
            .lateral("in_mss")
        )
        stmt = stmt.join_from(Listing, in_mss, true(), isouter=True)
        stmt = stmt.add_columns(
            in_mss.c.status_de.label("mss_status_de"),
            in_mss.c.dynamics_de.label("mss_dynamics_de"),
        )

        return stmt

    # -----------------------------------------------------------------
    # Per-listing context — fat enrichment for the detail panel
    # -----------------------------------------------------------------

    def context_for(self, location: WKBElement) -> ListingContext:
        """Return the full ListingContext blob for a single listing's location.

        Runs ~12 sub-queries sequentially. Each respects the `k=1 always
        returns, k=2..k only within cap` rule for the per-dataset caps in
        `distances.py`. Cemeteries are excluded from `nearest_parks` and
        weighted 0.5 in `greenery`.

        v1 is sequential; can be wrapped in `asyncio.gather` later if
        latency becomes a concern.
        """
        return ListingContext(
            transit=self._nearest_transit_stops(location, k=3),
            school_catchment=self._school_catchment(location),
            nearest_schools=self._nearest_schools(location, k=3),
            nearest_parks=self._nearest_parks(location, k=2),
            nearest_playground=self._nearest_playground(location),
            nearest_hospitals=self._nearest_hospitals(location, k=2),
            nearest_water=self._nearest_water(location),
            noise=self._noise_profile(location),
            greenery=self._greenery_profile(location),
            density=self._density_profile(location),
            mss=self._mss_profile(location),
            disabled_parking_count=self._disabled_parking_count(location),
        )

    # Per-dataset private helpers. Each follows the same shape:
    #   - k=1 always returns regardless of distance (so the user always
    #     gets an answer to "what's the closest school")
    #   - k=2..k only included if within the per-dataset cap (so we never
    #     show "there's a park 20km away")
    # Caps are in `distances.py`. Cemeteries handled in `_nearest_parks`
    # and `_greenery_profile` per the threshold doc.

    def _nearest_transit_stops(
        self, location: WKBElement, *, k: int = 3
    ) -> list[NearestTransitStop]:
        """Top-k nearest transit stops with decoded modes + walk minutes.

        k=1 always returns; k=2..k only if within CAP_TRANSIT_STOPS_M to keep
        the chip list useful ("there's a stop 1.5km away" beats "5km").
        """
        distance_m = geo_func.ST_Distance(
            cast(TransitStop.geom, Geography),
            cast(location, Geography),
        )
        stmt = (
            select(
                TransitStop.stop_id,
                TransitStop.name,
                TransitStop.modes_served,
                TransitStop.lines_served,
                cast(distance_m, Integer).label("distance_m"),
            )
            .order_by(TransitStop.geom.op("<->")(location))
            .limit(k)
        )
        rows = self.db.execute(stmt).all()
        if not rows:
            return []
        out: list[NearestTransitStop] = []
        for i, row in enumerate(rows):
            d = int(row.distance_m)
            # k=1 always; subsequent rows only if within the dataset cap.
            if i > 0 and d > CAP_TRANSIT_STOPS_M:
                break
            out.append(
                NearestTransitStop(
                    stop_id=row.stop_id,
                    name=row.name,
                    modes=decode_modes(list(row.modes_served)),
                    lines=list(row.lines_served),
                    distance_m=d,
                    walk_minutes=walk_minutes(d),
                )
            )
        return out

    def _school_catchment(
        self, location: WKBElement
    ) -> SchoolCatchmentInfo | None:
        """Return the primary-school catchment polygon containing the listing.

        Returns None if the listing falls outside coverage (catchments don't
        cover Berlin uniformly).
        """
        stmt = (
            select(
                SchoolCatchment.catchment_id,
                SchoolCatchment.school_number,
                SchoolCatchment.school_name,
            )
            .where(geo_func.ST_Contains(SchoolCatchment.geom, location))
            .limit(1)
        )
        row = self.db.execute(stmt).first()
        if row is None:
            return None
        return SchoolCatchmentInfo(
            catchment_id=row.catchment_id,
            school_number=row.school_number,
            school_name=row.school_name,
        )

    def _nearest_schools(
        self, location: WKBElement, *, k: int = 3
    ) -> list[NearestSchool]:
        """Top-k nearest schools. k=1 always; k=2..k within CAP_SCHOOLS_M."""
        distance_m = geo_func.ST_Distance(
            cast(School.geom, Geography),
            cast(location, Geography),
        )
        stmt = (
            select(
                School.name,
                School.school_type,
                School.operator,
                cast(distance_m, Integer).label("distance_m"),
            )
            .order_by(School.geom.op("<->")(location))
            .limit(k)
        )
        rows = self.db.execute(stmt).all()
        if not rows:
            return []
        out: list[NearestSchool] = []
        for i, row in enumerate(rows):
            d = int(row.distance_m)
            if i > 0 and d > CAP_SCHOOLS_M:
                break
            out.append(
                NearestSchool(
                    name=row.name,
                    school_type=row.school_type,
                    operator=row.operator,
                    distance_m=d,
                )
            )
        return out

    def _nearest_parks(
        self, location: WKBElement, *, k: int = 2
    ) -> list[NearestPark]:
        """k=2..k respects CAP_PARKS_M and EXCLUDES cemeteries.

        Cemeteries are kept out of named-nearest results per the threshold
        doc §5 (the user gets a useful chip like "🌳 Görlitzer Park 180m",
        not "🌳 Jüdischer Friedhof 200m"). They still contribute to
        `_greenery_profile` at 0.5 weight.
        """
        distance_m = geo_func.ST_Distance(
            cast(Park.geom, Geography),
            cast(location, Geography),
        )
        stmt = (
            select(
                Park.name,
                Park.object_type,
                Park.cadastral_area_m2,
                cast(distance_m, Integer).label("distance_m"),
            )
            .where(_not_cemetery())
            .order_by(Park.geom.op("<->")(location))
            .limit(k)
        )
        rows = self.db.execute(stmt).all()
        if not rows:
            return []
        out: list[NearestPark] = []
        for i, row in enumerate(rows):
            d = int(row.distance_m)
            if i > 0 and d > CAP_PARKS_M:
                break
            out.append(
                NearestPark(
                    name=row.name,
                    object_type=row.object_type,
                    distance_m=d,
                    area_m2=row.cadastral_area_m2,
                )
            )
        return out

    def _nearest_playground(
        self, location: WKBElement
    ) -> NearestPlayground | None:
        """Nearest playground within CAP_PLAYGROUNDS_M, else None."""
        distance_m = geo_func.ST_Distance(
            cast(Playground.geom, Geography),
            cast(location, Geography),
        )
        stmt = (
            select(
                Playground.name,
                Playground.play_area_m2,
                cast(distance_m, Integer).label("distance_m"),
            )
            .order_by(Playground.geom.op("<->")(location))
            .limit(1)
        )
        row = self.db.execute(stmt).first()
        if row is None:
            return None
        d = int(row.distance_m)
        if d > CAP_PLAYGROUNDS_M:
            return None
        return NearestPlayground(
            name=row.name,
            distance_m=d,
            play_area_m2=row.play_area_m2,
        )

    def _nearest_hospitals(
        self,
        location: WKBElement,
        *,
        k: int = 2,
        tier: str = "any",
    ) -> list[NearestHospital]:
        """Top-k nearest hospitals (optionally tier-filtered).

        `tier="any"` (the detail-enrichment default) shows specialty clinics
        alongside Krankenhausplan facilities. `tier="plan_hospital"` filters
        to the emergency-care network.
        """
        distance_m = geo_func.ST_Distance(
            cast(Hospital.geom, Geography),
            cast(location, Geography),
        )
        stmt = (
            select(
                Hospital.name,
                Hospital.tier,
                Hospital.total_beds,
                cast(distance_m, Integer).label("distance_m"),
            )
            .order_by(Hospital.geom.op("<->")(location))
            .limit(k)
        )
        if tier == "plan_hospital":
            stmt = stmt.where(Hospital.tier == "plan_hospital")
        rows = self.db.execute(stmt).all()
        if not rows:
            return []
        out: list[NearestHospital] = []
        for i, row in enumerate(rows):
            d = int(row.distance_m)
            if i > 0 and d > CAP_HOSPITALS_M:
                break
            # Tier is constrained by the CHECK to 'plan_hospital' / 'other';
            # cast to the Literal-typed field via str.
            out.append(
                NearestHospital(
                    name=row.name,
                    tier=row.tier,  # type: ignore[arg-type]
                    distance_m=d,
                    total_beds=row.total_beds,
                )
            )
        return out

    def _nearest_water(self, location: WKBElement) -> NearestWater | None:
        """Nearest water body within CAP_WATER_M, else None."""
        distance_m = geo_func.ST_Distance(
            cast(WaterBody.geom, Geography),
            cast(location, Geography),
        )
        stmt = (
            select(
                WaterBody.name,
                WaterBody.water_kind,
                cast(distance_m, Integer).label("distance_m"),
            )
            .order_by(WaterBody.geom.op("<->")(location))
            .limit(1)
        )
        row = self.db.execute(stmt).first()
        if row is None:
            return None
        d = int(row.distance_m)
        if d > CAP_WATER_M:
            return None
        return NearestWater(
            name=row.name,
            water_kind=row.water_kind,
            distance_m=d,
        )

    def _noise_profile(self, location: WKBElement) -> NoiseProfile | None:
        """Nearest noise sample → bucketed Lden label + raw lden components."""
        stmt = (
            select(
                StreetNoise2022.noise_total_lden,
                StreetNoise2022.noise_street_lden,
                StreetNoise2022.noise_rail_lden,
            )
            .order_by(StreetNoise2022.geom.op("<->")(location))
            .limit(1)
        )
        row = self.db.execute(stmt).first()
        if row is None:
            return None
        return NoiseProfile(
            label=bucket_noise(row.noise_total_lden),
            total_lden=row.noise_total_lden,
            street_lden=row.noise_street_lden,
            rail_lden=row.noise_rail_lden,
        )

    def _greenery_profile(
        self, location: WKBElement
    ) -> GreeneryProfile | None:
        """Greenery composite: WHO Europe 300m radius, cemeteries at 0.5 weight.

        ⚠️ Heavy query — runs ST_Area(ST_Intersection(...)) across all parks
        and playgrounds within ~300m. Acceptable here because context_for
        runs for a single listing at a time. Threshold doc §4 + §5.

        Note: ST_Buffer on a geography returns geography too; cast back to
        geometry so it composes with the parks `MULTIPOLYGON(4326)` geom in
        ST_Intersection. Source park polygons are 4326, so SRID matches.
        """
        # 300m geography buffer → geometry for parks/playgrounds in 4326.
        buffer_geom = func.ST_Buffer(cast(location, Geography), 300).cast(
            type_=Park.geom.type
        )

        # Park area (non-cemetery, weight 1.0)
        park_area_sum = (
            select(
                func.coalesce(
                    func.sum(
                        func.ST_Area(
                            cast(
                                func.ST_Intersection(Park.geom, buffer_geom),
                                Geography,
                            )
                        )
                    ),
                    0.0,
                )
            )
            .where(_not_cemetery())
            .where(geo_func.ST_Intersects(Park.geom, buffer_geom))
            .scalar_subquery()
        )

        # Cemetery area (weight 0.5)
        cemetery_area_sum = (
            select(
                func.coalesce(
                    func.sum(
                        func.ST_Area(
                            cast(
                                func.ST_Intersection(Park.geom, buffer_geom),
                                Geography,
                            )
                        )
                    ),
                    0.0,
                )
            )
            .where(Park.object_type.ilike(_FRIEDHOF_PATTERN))
            .where(geo_func.ST_Intersects(Park.geom, buffer_geom))
            .scalar_subquery()
        )

        # Playground area (weight 1.0)
        playground_area_sum = (
            select(
                func.coalesce(
                    func.sum(
                        func.ST_Area(
                            cast(
                                func.ST_Intersection(Playground.geom, buffer_geom),
                                Geography,
                            )
                        )
                    ),
                    0.0,
                )
            )
            .where(geo_func.ST_Intersects(Playground.geom, buffer_geom))
            .scalar_subquery()
        )

        row = self.db.execute(
            select(
                park_area_sum.label("park_m2"),
                cemetery_area_sum.label("cemetery_m2"),
                playground_area_sum.label("playground_m2"),
            )
        ).first()
        if row is None:
            return None
        total = (
            float(row.park_m2)
            + 0.5 * float(row.cemetery_m2)
            + float(row.playground_m2)
        )
        return GreeneryProfile(
            label=bucket_greenery(total),
            green_m2_within_300m=total,
        )

    def _density_profile(self, location: WKBElement) -> DensityProfile | None:
        """LOR polygon containing the listing → bucketed density + age bands."""
        stmt = (
            select(
                PopulationDensity2025.population,
                PopulationDensity2025.population_per_hectare,
                PopulationDensity2025.age_under_6,
                PopulationDensity2025.age_6_to_10,
                PopulationDensity2025.age_10_to_18,
                PopulationDensity2025.age_65_to_70,
                PopulationDensity2025.age_70_to_75,
                PopulationDensity2025.age_75_to_80,
                PopulationDensity2025.age_80_plus,
            )
            .where(geo_func.ST_Contains(PopulationDensity2025.geom, location))
            .limit(1)
        )
        row = self.db.execute(stmt).first()
        if row is None:
            return None
        pop = row.population
        age_under_18_pct = None
        age_65_plus_pct = None
        if pop and pop > 0:
            under_18 = (
                (row.age_under_6 or 0)
                + (row.age_6_to_10 or 0)
                + (row.age_10_to_18 or 0)
            )
            plus_65 = (
                (row.age_65_to_70 or 0)
                + (row.age_70_to_75 or 0)
                + (row.age_75_to_80 or 0)
                + (row.age_80_plus or 0)
            )
            age_under_18_pct = round(100 * under_18 / pop, 1)
            age_65_plus_pct = round(100 * plus_65 / pop, 1)
        return DensityProfile(
            label=bucket_density(row.population_per_hectare),
            persons_per_hectare=row.population_per_hectare,
            age_under_18_pct=age_under_18_pct,
            age_65_plus_pct=age_65_plus_pct,
        )

    def _mss_profile(self, location: WKBElement) -> MssProfile | None:
        """Sozialmonitoring planning area containing the listing → EN labels."""
        stmt = (
            select(
                SocialMonitoring2025.status_index_label,
                SocialMonitoring2025.dynamics_index_label,
                SocialMonitoring2025.social_inequality_label,
                SocialMonitoring2025.residents,
            )
            .where(geo_func.ST_Contains(SocialMonitoring2025.geom, location))
            .limit(1)
        )
        row = self.db.execute(stmt).first()
        if row is None:
            return None
        return MssProfile(
            status_label=MSS_STATUS_DE_TO_EN.get(row.status_index_label or ""),
            dynamics_label=MSS_DYNAMICS_DE_TO_EN.get(
                row.dynamics_index_label or ""
            ),
            social_inequality_label=row.social_inequality_label,
            residents=row.residents,
        )

    def _disabled_parking_count(
        self, location: WKBElement, *, radius_m: int = 300
    ) -> int:
        """Count of disabled-parking spots within `radius_m` meters."""
        stmt = select(func.count(DisabledParking.id)).where(
            geo_func.ST_DWithin(
                cast(DisabledParking.geom, Geography),
                cast(location, Geography),
                radius_m,
            )
        )
        return int(self.db.execute(stmt).scalar() or 0)
