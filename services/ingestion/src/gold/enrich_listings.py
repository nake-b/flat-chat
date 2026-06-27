"""Per-family enrichment functions for the gold layer.

Each function is a single bulk-SQL operation that produces one slice of
the per-listing geo-context. Set-based and idempotent: running the same
function twice yields the same result; running it once on a refreshed
silver source updates every listing in one pass.

Two output shapes:

  - **Junction tables** (`listings_nearby_*`): one row per (listing × POI)
    pair. Filled by the `enrich_nearby_*` functions. The search hot-path
    queries them with `EXISTS` + B-tree predicates; the detail panel reads
    top-N by `rank` via `ListingService.get()`. See
    `agent-compound-docs/decisions/spatial-neighbor-tables.md`.

  - **`listings_geo_context` columns**: scalar / field facts about the
    listing's location (noise dB, density, greenery m², school-catchment
    membership, disabled-parking count, inside-ring flag, bezirk/ortsteil) +
    a small set of "nearest X" chip scalars derived from the junction tables
    for cheap card-row label rendering. Filled by `enrich_noise`,
    `enrich_greenery`, `enrich_density`, `enrich_admin_areas`,
    `enrich_inside_ring`, `enrich_disabled_parking`, and `enrich_chip_scalars`.

Architectural notes:
  - Gold stores RAW measurements only (numbers, IDs, names). Bucket labels
    (`quiet`/`lively`/`noisy`, density categories, greenery class) are
    applied at the chat-presentation layer from `listings/labels.py` so
    threshold tweaks don't require a gold rebuild.
  - Per-feature storage radii (`R_NEARBY_*_M`) are generous on purpose —
    search-time predicates do the actual cutoff. See threshold doc §1.
  - Population rule per listing: top-K=5 always-include ∪ all features
    within R. K=5 guarantees the detail panel renders even in
    feature-sparse periphery.
  - Constants are inlined here because the ingestion service intentionally
    does NOT depend on the backend package.
  - Outer `ROW_NUMBER` windows tie-break on the feature id so two POIs at
    the same rounded distance get a stable rank — keeps `gold.run`
    idempotent (rank-1 chip scalars don't drift across re-runs) and stops
    the shadow-diff harness from flagging spurious churn.

Threshold doc: `agent-compound-docs/decisions/geo-context-thresholds.md`.
"""

from __future__ import annotations

import logging

from sqlalchemy import Connection, text

logger = logging.getLogger(__name__)


# -------------------------------------------------------------------------
# Per-family storage radii (meters). Generous; search-time predicates do
# the actual filter cutoff. See threshold doc §1.
# -------------------------------------------------------------------------

R_NEARBY_TRANSIT_M = 5_000
R_NEARBY_SCHOOLS_M = 5_000
R_NEARBY_HOSPITALS_M = 12_000
R_NEARBY_PARKS_M = 5_000
R_NEARBY_PLAYGROUNDS_M = 3_000
R_NEARBY_WATER_M = 6_000
# Kitas are denser + more hyperlocal than schools → mirror playgrounds'
# 3 km, not schools' 5 km. See threshold doc §1.
R_NEARBY_KITAS_M = 3_000
# Notable landmarks are sparse; "near a landmark" is generous. See
# threshold doc §1.
R_NEARBY_LANDMARKS_M = 2_000

# Landmark categories worth surfacing as a "near the X" card attribute.
# `building` (the bulk of ALKIS footprints) is intentionally excluded — a
# named building footprint is useful for `locate_place` search but too noisy
# as a nearby-landmark chip.
NOTABLE_LANDMARK_CATEGORIES = (
    "monument",
    "tower",
    "bridge",
    "stadium",
    "attraction",
)

# Top-K always-include — every listing carries at least this many rows per
# family so the detail panel renders even in feature-sparse periphery.
ALWAYS_INCLUDE_K = 5


# -------------------------------------------------------------------------
# Other constants
# -------------------------------------------------------------------------

GREENERY_BUFFER_M = 300
DISABLED_PARKING_RADIUS_M = 300

# Noise coverage gate. The strategic_noise_2022 dataset is a ~10m grid of
# modelled receivers along every Berlin road/rail; a well-geocoded listing
# almost always has a sample within 10m. The 50m gate is 5× headroom: it
# catches geocode drift without admitting readings from a different
# acoustic block (line-source attenuation is ~3 dB per doubling of
# distance, so 50m → 100m drops ~3 dB).
#
# Sources:
#   - https://www.sciencedirect.com/science/article/abs/pii/S0003682X14000693
#     (50m aggregation radius — mobile noise mapping)
#   - https://www.sciencedirect.com/science/article/pii/S0003682X22000664
#     (CNOSSOS-EU validation at sub-100m granularity)
#   - https://www.mdpi.com/2220-9964/11/8/441
#     (10×10m noise mapping resolution)
NOISE_COVERAGE_RADIUS_M = 50

# Cemetery exclusion — case-insensitive substring match on
# `parks.object_type`. See threshold doc §5.
FRIEDHOF_PATTERN = "%friedhof%"


# -------------------------------------------------------------------------
# Row seeding
# -------------------------------------------------------------------------


def ensure_rows(conn: Connection) -> int:
    """Make sure every listing has a (mostly-empty) row in `listings_geo_context`.

    Per-chip UPDATE statements need a target row to exist. New listings
    added by silver get seeded here on the next gold run; existing rows
    are left untouched.
    """
    result = conn.execute(
        text(
            """
            INSERT INTO listings_geo_context (listing_id)
            SELECT l.id
            FROM listings l
            LEFT JOIN listings_geo_context lgc ON lgc.listing_id = l.id
            WHERE lgc.listing_id IS NULL
              AND l.location IS NOT NULL
            ON CONFLICT (listing_id) DO NOTHING
            """
        )
    )
    return result.rowcount or 0


# =========================================================================
# Junction-table enrichers — one bulk DELETE + INSERT per family.
#
# Shape per function: TRUNCATE the table (or DELETE WHERE listing_id IN
# subquery if we ever want partial refresh) then INSERT one row per
# (listing × feature) pair with rank from a LATERAL KNN.
#
# Per-listing rule: LIMIT GREATEST(K, count_within_R). K=5 means even a
# transit-desert listing carries 5 rows for the detail panel; within-R
# means dense neighbourhoods carry ~hundreds for the filter use-case.
# =========================================================================


def enrich_nearby_transit(conn: Connection) -> int:
    """Listings × transit stops within R = 5 km (top-K=5 always)."""
    conn.execute(text("TRUNCATE TABLE listings_nearby_transit"))
    result = conn.execute(
        text(
            """
            INSERT INTO listings_nearby_transit (
                listing_id, stop_id, distance_m, modes, lines, name, rank
            )
            SELECT l.id,
                   ts.stop_id,
                   ts.distance_m,
                   ts.modes_served::int[],
                   ts.lines_served,
                   ts.name,
                   ts.rank::smallint
            FROM listings l
            CROSS JOIN LATERAL (
                SELECT t.stop_id, t.name, t.modes_served, t.lines_served,
                       t.distance_m,
                       ROW_NUMBER() OVER (
                           ORDER BY t.distance_m, t.stop_id
                       ) AS rank
                FROM (
                    SELECT ts.stop_id, ts.name, ts.modes_served, ts.lines_served,
                           ST_Distance(ts.geom::geography,
                                       l.location::geography)::int AS distance_m
                    FROM transit_stops ts
                    ORDER BY ts.geom <-> l.location
                    LIMIT GREATEST(
                        :k,
                        (SELECT COUNT(*) FROM transit_stops ts2
                         WHERE ST_DWithin(ts2.geom::geography,
                                          l.location::geography,
                                          :radius_m))
                    )
                ) t
            ) ts
            WHERE l.location IS NOT NULL
            """
        ),
        {"k": ALWAYS_INCLUDE_K, "radius_m": R_NEARBY_TRANSIT_M},
    )
    return result.rowcount or 0


def enrich_nearby_schools(conn: Connection) -> int:
    """Listings × schools within R = 5 km (top-K=5 always)."""
    conn.execute(text("TRUNCATE TABLE listings_nearby_schools"))
    result = conn.execute(
        text(
            """
            INSERT INTO listings_nearby_schools (
                listing_id, school_id, distance_m, school_type, name, rank
            )
            SELECT l.id,
                   s.school_id,
                   s.distance_m,
                   s.school_type,
                   s.name,
                   s.rank::smallint
            FROM listings l
            CROSS JOIN LATERAL (
                SELECT t.school_id, t.name, t.school_type,
                       t.distance_m,
                       ROW_NUMBER() OVER (
                           ORDER BY t.distance_m, t.school_id
                       ) AS rank
                FROM (
                    SELECT s.id::text AS school_id, s.name, s.school_type,
                           ST_Distance(s.geom::geography,
                                       l.location::geography)::int AS distance_m
                    FROM schools s
                    WHERE s.geom IS NOT NULL
                    ORDER BY s.geom <-> l.location
                    LIMIT GREATEST(
                        :k,
                        (SELECT COUNT(*) FROM schools s2
                         WHERE s2.geom IS NOT NULL
                           AND ST_DWithin(s2.geom::geography,
                                          l.location::geography,
                                          :radius_m))
                    )
                ) t
            ) s
            WHERE l.location IS NOT NULL
            """
        ),
        {"k": ALWAYS_INCLUDE_K, "radius_m": R_NEARBY_SCHOOLS_M},
    )
    return result.rowcount or 0


def enrich_nearby_hospitals(conn: Connection) -> int:
    """Listings × hospitals within R = 12 km (top-K=5 always)."""
    conn.execute(text("TRUNCATE TABLE listings_nearby_hospitals"))
    result = conn.execute(
        text(
            """
            INSERT INTO listings_nearby_hospitals (
                listing_id, hospital_id, distance_m, tier, name, rank
            )
            SELECT l.id,
                   h.hospital_id,
                   h.distance_m,
                   h.tier,
                   h.name,
                   h.rank::smallint
            FROM listings l
            CROSS JOIN LATERAL (
                SELECT t.hospital_id, t.name, t.tier,
                       t.distance_m,
                       ROW_NUMBER() OVER (
                           ORDER BY t.distance_m, t.hospital_id
                       ) AS rank
                FROM (
                    SELECT h.id::text AS hospital_id, h.name, h.tier,
                           ST_Distance(h.geom::geography,
                                       l.location::geography)::int AS distance_m
                    FROM hospitals h
                    WHERE h.geom IS NOT NULL
                    ORDER BY h.geom <-> l.location
                    LIMIT GREATEST(
                        :k,
                        (SELECT COUNT(*) FROM hospitals h2
                         WHERE h2.geom IS NOT NULL
                           AND ST_DWithin(h2.geom::geography,
                                          l.location::geography,
                                          :radius_m))
                    )
                ) t
            ) h
            WHERE l.location IS NOT NULL
            """
        ),
        {"k": ALWAYS_INCLUDE_K, "radius_m": R_NEARBY_HOSPITALS_M},
    )
    return result.rowcount or 0


def enrich_nearby_parks(conn: Connection) -> int:
    """Listings × parks within R = 5 km (top-K=5 always). Cemeteries excluded."""
    conn.execute(text("TRUNCATE TABLE listings_nearby_parks"))
    result = conn.execute(
        text(
            """
            INSERT INTO listings_nearby_parks (
                listing_id, park_id, distance_m, object_type, name, rank
            )
            SELECT l.id,
                   p.park_id,
                   p.distance_m,
                   p.object_type,
                   p.name,
                   p.rank::smallint
            FROM listings l
            CROSS JOIN LATERAL (
                SELECT t.park_id, t.name, t.object_type,
                       t.distance_m,
                       ROW_NUMBER() OVER (
                           ORDER BY t.distance_m, t.park_id
                       ) AS rank
                FROM (
                    SELECT p.id::text AS park_id, p.name, p.object_type,
                           ST_Distance(p.geom::geography,
                                       l.location::geography)::int AS distance_m
                    FROM parks p
                    WHERE p.geom IS NOT NULL
                      AND p.object_type NOT ILIKE :friedhof
                    ORDER BY p.geom <-> l.location
                    LIMIT GREATEST(
                        :k,
                        (SELECT COUNT(*) FROM parks p2
                         WHERE p2.geom IS NOT NULL
                           AND p2.object_type NOT ILIKE :friedhof
                           AND ST_DWithin(p2.geom::geography,
                                          l.location::geography,
                                          :radius_m))
                    )
                ) t
            ) p
            WHERE l.location IS NOT NULL
            """
        ),
        {
            "k": ALWAYS_INCLUDE_K,
            "radius_m": R_NEARBY_PARKS_M,
            "friedhof": FRIEDHOF_PATTERN,
        },
    )
    return result.rowcount or 0


def enrich_nearby_playgrounds(conn: Connection) -> int:
    """Listings × playgrounds within R = 3 km (top-K=5 always)."""
    conn.execute(text("TRUNCATE TABLE listings_nearby_playgrounds"))
    result = conn.execute(
        text(
            """
            INSERT INTO listings_nearby_playgrounds (
                listing_id, playground_id, distance_m, name, rank
            )
            SELECT l.id,
                   pg.playground_id,
                   pg.distance_m,
                   pg.name,
                   pg.rank::smallint
            FROM listings l
            CROSS JOIN LATERAL (
                SELECT t.playground_id, t.name,
                       t.distance_m,
                       ROW_NUMBER() OVER (
                           ORDER BY t.distance_m, t.playground_id
                       ) AS rank
                FROM (
                    SELECT pg.id::text AS playground_id, pg.name,
                           ST_Distance(pg.geom::geography,
                                       l.location::geography)::int AS distance_m
                    FROM playgrounds pg
                    WHERE pg.geom IS NOT NULL
                    ORDER BY pg.geom <-> l.location
                    LIMIT GREATEST(
                        :k,
                        (SELECT COUNT(*) FROM playgrounds pg2
                         WHERE pg2.geom IS NOT NULL
                           AND ST_DWithin(pg2.geom::geography,
                                          l.location::geography,
                                          :radius_m))
                    )
                ) t
            ) pg
            WHERE l.location IS NOT NULL
            """
        ),
        {"k": ALWAYS_INCLUDE_K, "radius_m": R_NEARBY_PLAYGROUNDS_M},
    )
    return result.rowcount or 0


def enrich_nearby_water(conn: Connection) -> int:
    """Listings × water bodies within R = 6 km (top-K=5 always)."""
    conn.execute(text("TRUNCATE TABLE listings_nearby_water"))
    result = conn.execute(
        text(
            """
            INSERT INTO listings_nearby_water (
                listing_id, water_id, distance_m, water_kind, name, rank
            )
            SELECT l.id,
                   w.water_id,
                   w.distance_m,
                   w.water_kind,
                   w.name,
                   w.rank::smallint
            FROM listings l
            CROSS JOIN LATERAL (
                SELECT t.water_id, t.name, t.water_kind,
                       t.distance_m,
                       ROW_NUMBER() OVER (
                           ORDER BY t.distance_m, t.water_id
                       ) AS rank
                FROM (
                    SELECT w.id::text AS water_id, w.name, w.water_kind,
                           ST_Distance(w.geom::geography,
                                       l.location::geography)::int AS distance_m
                    FROM water_bodies w
                    WHERE w.geom IS NOT NULL
                    ORDER BY w.geom <-> l.location
                    LIMIT GREATEST(
                        :k,
                        (SELECT COUNT(*) FROM water_bodies w2
                         WHERE w2.geom IS NOT NULL
                           AND ST_DWithin(w2.geom::geography,
                                          l.location::geography,
                                          :radius_m))
                    )
                ) t
            ) w
            WHERE l.location IS NOT NULL
            """
        ),
        {"k": ALWAYS_INCLUDE_K, "radius_m": R_NEARBY_WATER_M},
    )
    return result.rowcount or 0


def enrich_nearby_kitas(conn: Connection) -> int:
    """Listings × kitas within R = 3 km (top-K=5 always)."""
    conn.execute(text("TRUNCATE TABLE listings_nearby_kitas"))
    result = conn.execute(
        text(
            """
            INSERT INTO listings_nearby_kitas (
                listing_id, kita_id, distance_m, name, rank
            )
            SELECT l.id,
                   k.kita_id,
                   k.distance_m,
                   k.name,
                   k.rank::smallint
            FROM listings l
            CROSS JOIN LATERAL (
                SELECT t.kita_id, t.name,
                       t.distance_m,
                       ROW_NUMBER() OVER (
                           ORDER BY t.distance_m, t.kita_id
                       ) AS rank
                FROM (
                    SELECT k.id::text AS kita_id, k.name,
                           ST_Distance(k.geom::geography,
                                       l.location::geography)::int AS distance_m
                    FROM kitas k
                    WHERE k.geom IS NOT NULL
                    ORDER BY k.geom <-> l.location
                    LIMIT GREATEST(
                        :k,
                        (SELECT COUNT(*) FROM kitas k2
                         WHERE k2.geom IS NOT NULL
                           AND ST_DWithin(k2.geom::geography,
                                          l.location::geography,
                                          :radius_m))
                    )
                ) t
            ) k
            WHERE l.location IS NOT NULL
            """
        ),
        {"k": ALWAYS_INCLUDE_K, "radius_m": R_NEARBY_KITAS_M},
    )
    return result.rowcount or 0


def enrich_nearby_landmarks(conn: Connection) -> int:
    """Listings × notable landmarks within R = 2 km (top-K=5 always).

    Restricted to `NOTABLE_LANDMARK_CATEGORIES` — generic ALKIS building
    footprints (category='building') are excluded from the card attribute;
    they remain searchable by name via the `named_places` view.
    """
    conn.execute(text("TRUNCATE TABLE listings_nearby_landmarks"))
    result = conn.execute(
        text(
            """
            INSERT INTO listings_nearby_landmarks (
                listing_id, landmark_id, distance_m, category, name, rank
            )
            SELECT l.id,
                   lm.landmark_id,
                   lm.distance_m,
                   lm.category,
                   lm.name,
                   lm.rank::smallint
            FROM listings l
            CROSS JOIN LATERAL (
                SELECT t.landmark_id, t.name, t.category,
                       t.distance_m,
                       ROW_NUMBER() OVER (
                           ORDER BY t.distance_m, t.landmark_id
                       ) AS rank
                FROM (
                    SELECT lm.id::text AS landmark_id, lm.name, lm.category,
                           ST_Distance(lm.geom::geography,
                                       l.location::geography)::int AS distance_m
                    FROM landmarks lm
                    WHERE lm.geom IS NOT NULL
                      AND lm.category = ANY(:categories)
                    ORDER BY lm.geom <-> l.location
                    LIMIT GREATEST(
                        :k,
                        (SELECT COUNT(*) FROM landmarks lm2
                         WHERE lm2.geom IS NOT NULL
                           AND lm2.category = ANY(:categories)
                           AND ST_DWithin(lm2.geom::geography,
                                          l.location::geography,
                                          :radius_m))
                    )
                ) t
            ) lm
            WHERE l.location IS NOT NULL
            """
        ),
        {
            "k": ALWAYS_INCLUDE_K,
            "radius_m": R_NEARBY_LANDMARKS_M,
            "categories": list(NOTABLE_LANDMARK_CATEGORIES),
        },
    )
    return result.rowcount or 0


# =========================================================================
# Chip scalars — denormalised "nearest X" summary derived from the
# junction tables. One UPDATE per family on `listings_geo_context`. Used
# by the card-row projection (`_row_to_uiapartment`) for cheap label
# rendering ("10 min to park"); the authoritative source remains the
# junction table.
# =========================================================================


def enrich_chip_scalars(conn: Connection) -> int:
    """Derive `nearest_transit_*` + `nearest_park_*` from junction tables."""
    rowcount = 0

    # Transit chip: nearest stop's distance, lines, name.
    result = conn.execute(
        text(
            """
            UPDATE listings_geo_context lgc
            SET nearest_transit_m     = nt.distance_m,
                nearest_transit_lines = nt.lines,
                nearest_transit_name  = nt.name,
                enriched_at           = now()
            FROM listings_nearby_transit nt
            WHERE nt.listing_id = lgc.listing_id
              AND nt.rank = 1
            """
        )
    )
    rowcount += result.rowcount or 0

    # Park chip: nearest non-cemetery park's distance + name.
    result = conn.execute(
        text(
            """
            UPDATE listings_geo_context lgc
            SET nearest_park_m    = np.distance_m,
                nearest_park_name = np.name,
                enriched_at       = now()
            FROM listings_nearby_parks np
            WHERE np.listing_id = lgc.listing_id
              AND np.rank = 1
            """
        )
    )
    rowcount += result.rowcount or 0

    return rowcount


# =========================================================================
# Scalar / field enrichers — properties of the listing's location, not
# POI sets. Stay on `listings_geo_context`.
# =========================================================================


def enrich_noise(conn: Connection) -> int:
    """Nearest noise sample within the 50 m coverage gate.

    Two-stage filter for performance:
      1. Bbox pre-filter via `ST_DWithin(n.geom, l.location, <degrees>)`
         — uses the GIST index on `strategic_noise_2022.geom` directly.
         The bbox radius is the gate (50 m) translated to degrees at
         Berlin's latitude (~52.5°N: 50 / 111320 ≈ 0.00045 lat, 50 /
         (111320 * cos(52.5°)) ≈ 0.00074 lon). We use 0.001 as a
         conservative one-value cover for both axes.
      2. Exact geography distance verified via the LATERAL's `ON`
         condition — kept only if ≤ 50 m on the great-circle.

    NULL output means "no trusted reading within `NOISE_COVERAGE_RADIUS_M`"
    — usually a listing with bad coordinates. The search filter then
    optimistic-includes the listing.
    """
    # 50 m in degrees at Berlin latitude — see docstring derivation.
    # Slightly over-covers; the exact geography check below trims to 50 m.
    bbox_deg = 0.001
    result = conn.execute(
        text(
            """
            UPDATE listings_geo_context lgc
            SET noise_total_lden   = nearest.total_lden,
                noise_total_lnight = nearest.total_lnight,
                noise_profile      = nearest.blob,
                enriched_at        = now()
            FROM listings l
            LEFT JOIN LATERAL (
                SELECT n.noise_total_lden AS total_lden,
                       n.noise_total_lnight AS total_lnight,
                       ST_Distance(n.geom::geography,
                                   l.location::geography)::int AS distance_m,
                       jsonb_build_object(
                           'total_lden',    n.noise_total_lden,
                           'total_lnight',  n.noise_total_lnight,
                           'street_lden',   n.noise_street_lden,
                           'rail_lden',     n.noise_rail_lden,
                           'distance_m',    ST_Distance(n.geom::geography,
                                                        l.location::geography)::int
                       ) AS blob
                FROM strategic_noise_2022 n
                WHERE l.location IS NOT NULL
                  AND ST_DWithin(n.geom, l.location, :bbox_deg)
                ORDER BY n.geom <-> l.location
                LIMIT 1
            ) nearest ON nearest.distance_m <= :gate
            WHERE lgc.listing_id = l.id
            """
        ),
        {"gate": NOISE_COVERAGE_RADIUS_M, "bbox_deg": bbox_deg},
    )
    return result.rowcount or 0


def enrich_greenery(conn: Connection) -> int:
    """Greenery composite: m² of (parks + playgrounds + 0.5 * cemeteries) ∩ 300m."""
    result = conn.execute(
        text(
            """
            UPDATE listings_geo_context lgc
            SET greenery_profile = jsonb_build_object(
                    'green_m2_within_300m',
                    COALESCE(park_m2, 0) + 0.5 * COALESCE(cem_m2, 0) + COALESCE(pg_m2, 0)
                ),
                enriched_at = now()
            FROM listings l,
            LATERAL (
                SELECT ST_Buffer(l.location::geography, :buffer_m)::geometry AS buf
            ) buffer,
            LATERAL (
                SELECT
                    (
                        SELECT COALESCE(SUM(
                            ST_Area(ST_Intersection(p.geom, buffer.buf)::geography)
                        ), 0)
                        FROM parks p
                        WHERE p.object_type NOT ILIKE :friedhof
                          AND ST_Intersects(p.geom, buffer.buf)
                    ) AS park_m2,
                    (
                        SELECT COALESCE(SUM(
                            ST_Area(ST_Intersection(p.geom, buffer.buf)::geography)
                        ), 0)
                        FROM parks p
                        WHERE p.object_type ILIKE :friedhof
                          AND ST_Intersects(p.geom, buffer.buf)
                    ) AS cem_m2,
                    (
                        SELECT COALESCE(SUM(
                            ST_Area(ST_Intersection(pg.geom, buffer.buf)::geography)
                        ), 0)
                        FROM playgrounds pg
                        WHERE ST_Intersects(pg.geom, buffer.buf)
                    ) AS pg_m2
            ) sums
            WHERE lgc.listing_id = l.id
              AND l.location IS NOT NULL
            """
        ),
        {"buffer_m": GREENERY_BUFFER_M, "friedhof": FRIEDHOF_PATTERN},
    )
    return result.rowcount or 0


def enrich_density(conn: Connection) -> int:
    """LOR-area population density (chip) + age-bucket profile."""
    result = conn.execute(
        text(
            """
            UPDATE listings_geo_context lgc
            SET persons_per_hectare = d.population_per_hectare,
                density_profile = jsonb_build_object(
                    'persons_per_hectare', d.population_per_hectare,
                    'population',          d.population,
                    'age_under_6',         d.age_under_6,
                    'age_6_to_10',         d.age_6_to_10,
                    'age_10_to_18',        d.age_10_to_18,
                    'age_18_to_65',        d.age_18_to_65,
                    'age_65_to_70',        d.age_65_to_70,
                    'age_70_to_75',        d.age_70_to_75,
                    'age_75_to_80',        d.age_75_to_80,
                    'age_80_plus',         d.age_80_plus
                ),
                enriched_at = now()
            FROM listings l, population_density_2025 d
            WHERE lgc.listing_id = l.id
              AND l.location IS NOT NULL
              AND ST_Contains(d.geom, l.location)
            """
        )
    )
    return result.rowcount or 0


def enrich_admin_areas(conn: Connection) -> int:
    """Assign the listing's Bezirk + Ortsteil from the ALKIS polygons.

    For each, pick the SMALLEST containing polygon (ORDER BY ST_Area ASC
    LIMIT 1) so overlapping/nested boundaries resolve to the most specific
    area. LEFT JOIN LATERAL per polygon table — a listing outside coverage
    keeps NULL rather than dropping out of the UPDATE.
    """
    result = conn.execute(
        text(
            """
            UPDATE listings_geo_context lgc
            SET listing_bezirk   = bz.name,
                listing_ortsteil = ot.name,
                enriched_at      = now()
            FROM listings l
            LEFT JOIN LATERAL (
                SELECT b.name
                FROM bezirke b
                WHERE l.location IS NOT NULL
                  AND ST_Covers(b.geom, l.location)
                ORDER BY ST_Area(b.geom) ASC
                LIMIT 1
            ) bz ON true
            LEFT JOIN LATERAL (
                SELECT o.name
                FROM ortsteile o
                WHERE l.location IS NOT NULL
                  AND ST_Covers(o.geom, l.location)
                ORDER BY ST_Area(o.geom) ASC
                LIMIT 1
            ) ot ON true
            WHERE lgc.listing_id = l.id
            """
        )
    )
    return result.rowcount or 0


def enrich_inside_ring(conn: Connection) -> int:
    """Flag whether the listing falls inside the low-emission zone (≈ ring).

    The zone is normally a single feature in `inner_city_zone`; an `EXISTS`
    over the table yields a clean boolean per listing and stays correct even
    if the source ever arrives multipart (a `LIMIT 1` would silently test one
    arbitrary feature).
    """
    result = conn.execute(
        text(
            """
            UPDATE listings_geo_context lgc
            SET inside_ring = EXISTS (
                    SELECT 1 FROM inner_city_zone z
                    WHERE ST_Contains(z.geom, l.location)
                ),
                enriched_at = now()
            FROM listings l
            WHERE lgc.listing_id = l.id
              AND l.location IS NOT NULL
            """
        )
    )
    return result.rowcount or 0


def enrich_school_catchment(conn: Connection) -> int:
    """Primary-school catchment polygon membership (legal-attendance signal)."""
    result = conn.execute(
        text(
            """
            UPDATE listings_geo_context lgc
            SET school_catchment = catchment.blob,
                enriched_at      = now()
            FROM listings l
            LEFT JOIN LATERAL (
                SELECT jsonb_build_object(
                    'catchment_id',  sc.catchment_id,
                    'school_number', sc.school_number,
                    'school_name',   sc.school_name
                ) AS blob
                FROM school_catchments sc
                WHERE l.location IS NOT NULL
                  AND ST_Contains(sc.geom, l.location)
                LIMIT 1
            ) catchment ON true
            WHERE lgc.listing_id = l.id
            """
        )
    )
    return result.rowcount or 0


def enrich_disabled_parking(conn: Connection) -> int:
    """Count of disabled-parking spots within 300m."""
    result = conn.execute(
        text(
            """
            UPDATE listings_geo_context lgc
            SET disabled_parking_count = COALESCE(c.cnt, 0),
                enriched_at = now()
            FROM listings l
            LEFT JOIN LATERAL (
                SELECT COUNT(*) AS cnt
                FROM disabled_parking dp
                WHERE l.location IS NOT NULL
                  AND ST_DWithin(dp.geom::geography,
                                 l.location::geography,
                                 :radius)
            ) c ON true
            WHERE lgc.listing_id = l.id
            """
        ),
        {"radius": DISABLED_PARKING_RADIUS_M},
    )
    return result.rowcount or 0


# -------------------------------------------------------------------------
# Registry — used by `gold.run` to dispatch `--only` family filtering.
# Order matters for `chip_scalars`: it reads from the nearby_* tables, so
# they must run first.
# -------------------------------------------------------------------------


CHIP_FAMILIES = {
    "nearby_transit": enrich_nearby_transit,
    "nearby_schools": enrich_nearby_schools,
    "nearby_kitas": enrich_nearby_kitas,
    "nearby_hospitals": enrich_nearby_hospitals,
    "nearby_parks": enrich_nearby_parks,
    "nearby_playgrounds": enrich_nearby_playgrounds,
    "nearby_water": enrich_nearby_water,
    "nearby_landmarks": enrich_nearby_landmarks,
    "chip_scalars": enrich_chip_scalars,
    "noise": enrich_noise,
    "greenery": enrich_greenery,
    "density": enrich_density,
    "admin_areas": enrich_admin_areas,
    "inside_ring": enrich_inside_ring,
    "school_catchment": enrich_school_catchment,
    "disabled_parking": enrich_disabled_parking,
}
