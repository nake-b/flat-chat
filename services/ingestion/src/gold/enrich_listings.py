"""Per-chip-family enrichment functions for the gold layer.

Each function is a single bulk-SQL UPSERT against `listings_geo_context`,
joining `listings.location` to a silver geo-context table to compute the
chip(s) and detail-blob(s) that family owns.

Set-based and idempotent: running the same function twice yields the same
result; running it once on a refreshed silver source updates every listing
in one pass. Postgres uses the existing GIST indexes on silver tables via
the `<->` operator (KNN) and `ST_Contains` predicate.

Architectural notes:
  - Gold stores RAW measurements only (numbers, IDs, names). Bucket labels
    (`quiet`/`lively`/`noisy`, density categories, greenery class) are
    applied at the chat-presentation layer from `listings/labels.py` so
    threshold tweaks don't require a gold rebuild.
  - JSONB detail blobs are stored as ordered arrays (nearest → farthest)
    of fully-typed dicts. The shapes match `flat_chat.listings.context`
    Pydantic models 1:1; `ListingService.get()` parses them with no
    transformation.
  - Cap distances are inlined as constants (with a comment pointing to the
    threshold doc) because this module is in the ingestion service which
    intentionally does NOT depend on backend code.

Threshold doc: `agent-compound-docs/decisions/geo-context-thresholds.md`.
"""

from __future__ import annotations

import logging

from sqlalchemy import Connection, text
from .enrich_buildings import enrich_buildings

logger = logging.getLogger(__name__)


# -------------------------------------------------------------------------
# Cap distances (meters). Mirror `listings/thresholds.py` in the backend;
# kept inline here so the ingestion service doesn't import from backend.
# See agent-compound-docs/decisions/geo-context-thresholds.md §1.
# -------------------------------------------------------------------------

CAP_TRANSIT_STOPS_M = 1500
CAP_SCHOOLS_M = 2500
CAP_PARKS_M = 1500
CAP_PLAYGROUNDS_M = 1000
CAP_HOSPITALS_M = 5000
CAP_WATER_M = 2000
GREENERY_BUFFER_M = 300
DISABLED_PARKING_RADIUS_M = 300

# Cemetery exclusion — case-insensitive substring match on
# `parks.object_type`. See threshold doc §5.
FRIEDHOF_PATTERN = "%friedhof%"


# -------------------------------------------------------------------------
# Row seeding
# -------------------------------------------------------------------------


def ensure_rows(conn: Connection) -> int:
    """Make sure every listing has a (mostly-empty) row in gold.

    Per-chip UPDATE statements need a target row to exist. New listings
    added by silver get seeded here on the next gold run; existing rows
    are left untouched (the chip UPDATEs refresh enriched_at).
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


# -------------------------------------------------------------------------
# Transit  ← silver.transit_stops
# -------------------------------------------------------------------------


def enrich_transit(conn: Connection) -> int:
    """Nearest transit stop (chip) + top 3 (detail blob)."""
    result = conn.execute(
        text(
            """
            UPDATE listings_geo_context lgc
            SET nearest_transit_lines = nearest.lines_served,
                nearest_transit_m     = nearest.distance_m,
                nearest_transit_name  = nearest.name,
                transit_top3          = top3.blob,
                enriched_at           = now()
            FROM listings l
            LEFT JOIN LATERAL (
                SELECT ts.lines_served,
                       ts.name,
                       ST_Distance(ts.geom::geography,
                                   l.location::geography)::int AS distance_m
                FROM transit_stops ts
                WHERE l.location IS NOT NULL
                ORDER BY ts.geom <-> l.location
                LIMIT 1
            ) nearest ON true
            LEFT JOIN LATERAL (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'stop_id',    t.stop_id,
                        'name',       t.name,
                        'modes',      t.modes_served,
                        'lines',      t.lines_served,
                        'distance_m', t.distance_m
                    )
                    ORDER BY t.distance_m
                ) AS blob
                FROM (
                    SELECT ts.stop_id, ts.name, ts.modes_served, ts.lines_served,
                           ST_Distance(ts.geom::geography,
                                       l.location::geography)::int AS distance_m
                    FROM transit_stops ts
                    WHERE l.location IS NOT NULL
                    ORDER BY ts.geom <-> l.location
                    LIMIT 3
                ) t
            ) top3 ON true
            WHERE lgc.listing_id = l.id
            """
        )
    )
    return result.rowcount or 0


# -------------------------------------------------------------------------
# Parks  ← silver.parks  (cemeteries excluded, see threshold doc §5)
# -------------------------------------------------------------------------


def enrich_parks(conn: Connection) -> int:
    """Nearest park (chip) + top 2 (detail blob). Cemeteries excluded."""
    result = conn.execute(
        text(
            """
            UPDATE listings_geo_context lgc
            SET nearest_park_name = nearest.name,
                nearest_park_m    = nearest.distance_m,
                parks_top2        = top2.blob,
                enriched_at       = now()
            FROM listings l
            LEFT JOIN LATERAL (
                SELECT p.name,
                       ST_Distance(p.geom::geography,
                                   l.location::geography)::int AS distance_m
                FROM parks p
                WHERE l.location IS NOT NULL
                  AND p.object_type NOT ILIKE :friedhof
                ORDER BY p.geom <-> l.location
                LIMIT 1
            ) nearest ON true
            LEFT JOIN LATERAL (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'name',       t.name,
                        'distance_m', t.distance_m
                    )
                    ORDER BY t.distance_m
                ) AS blob
                FROM (
                    SELECT p.name,
                           ST_Distance(p.geom::geography,
                                       l.location::geography)::int AS distance_m
                    FROM parks p
                    WHERE l.location IS NOT NULL
                      AND p.object_type NOT ILIKE :friedhof
                    ORDER BY p.geom <-> l.location
                    LIMIT 2
                ) t
            ) top2 ON true
            WHERE lgc.listing_id = l.id
            """
        ),
        {"friedhof": FRIEDHOF_PATTERN},
    )
    return result.rowcount or 0


# -------------------------------------------------------------------------
# Playground  ← silver.playgrounds
# -------------------------------------------------------------------------


def enrich_playground(conn: Connection) -> int:
    """Nearest playground (detail blob only — no card-level chip)."""
    result = conn.execute(
        text(
            """
            UPDATE listings_geo_context lgc
            SET playground = nearest.blob,
                enriched_at = now()
            FROM listings l
            LEFT JOIN LATERAL (
                SELECT jsonb_build_object(
                    'name',       pg.name,
                    'distance_m', ST_Distance(pg.geom::geography,
                                              l.location::geography)::int
                ) AS blob
                FROM playgrounds pg
                WHERE l.location IS NOT NULL
                ORDER BY pg.geom <-> l.location
                LIMIT 1
            ) nearest ON true
            WHERE lgc.listing_id = l.id
            """
        )
    )
    return result.rowcount or 0


# -------------------------------------------------------------------------
# Schools  ← silver.schools + school_catchments
# -------------------------------------------------------------------------


def enrich_schools(conn: Connection) -> int:
    """School catchment polygon (ST_Contains) + top 3 nearest schools."""
    result = conn.execute(
        text(
            """
            UPDATE listings_geo_context lgc
            SET school_catchment = catchment.blob,
                schools_top3     = top3.blob,
                enriched_at      = now()
            FROM listings l
            LEFT JOIN LATERAL (
                SELECT jsonb_build_object(
                    'catchment_id', sc.catchment_id,
                    'school_number', sc.school_number,
                    'school_name', sc.school_name
                ) AS blob
                FROM school_catchments sc
                WHERE l.location IS NOT NULL
                  AND ST_Contains(sc.geom, l.location)
                LIMIT 1
            ) catchment ON true
            LEFT JOIN LATERAL (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'name',        t.name,
                        'school_type', t.school_type,
                        'distance_m',  t.distance_m
                    )
                    ORDER BY t.distance_m
                ) AS blob
                FROM (
                    SELECT s.name, s.school_type,
                           ST_Distance(s.geom::geography,
                                       l.location::geography)::int AS distance_m
                    FROM schools s
                    WHERE l.location IS NOT NULL
                    ORDER BY s.geom <-> l.location
                    LIMIT 3
                ) t
            ) top3 ON true
            WHERE lgc.listing_id = l.id
            """
        )
    )
    return result.rowcount or 0


# -------------------------------------------------------------------------
# Kitas  ← silver.kitas
# -------------------------------------------------------------------------


def enrich_kitas(conn: Connection) -> int:
    """Nearest Kita chip + count within 500m + top 3 nearest Kitas."""
    result = conn.execute(
        text(
            """
            UPDATE listings_geo_context lgc
            SET nearest_kita_m = nearest.distance_m,
                kitas_within_500_count = COALESCE(cnt.cnt, 0),
                kitas_top3     = top3.blob,
                enriched_at    = now()
            FROM listings l
            LEFT JOIN LATERAL (
                SELECT ST_Distance(k.geom::geography,
                                   l.location::geography)::int AS distance_m
                FROM kitas k
                WHERE l.location IS NOT NULL
                ORDER BY k.geom <-> l.location
                LIMIT 1
            ) nearest ON true
            LEFT JOIN LATERAL (
                SELECT COUNT(*) AS cnt
                FROM kitas k
                WHERE l.location IS NOT NULL
                  AND ST_DWithin(
                        k.geom::geography,
                        l.location::geography,
                        500
                  )
            ) cnt ON true
            LEFT JOIN LATERAL (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'name',      t.name,
                        'operator',  t.operator,
                        'address',   t.address,
                        'district',  t.district,
                        'distance_m', t.distance_m
                    )
                    ORDER BY t.distance_m
                ) AS blob
                FROM (
                    SELECT k.name, k.operator, k.address, k.district,
                           ST_Distance(k.geom::geography,
                                       l.location::geography)::int AS distance_m
                    FROM kitas k
                    WHERE l.location IS NOT NULL
                    ORDER BY k.geom <-> l.location
                    LIMIT 3
                ) t
            ) top3 ON true
            WHERE lgc.listing_id = l.id
            """
        )
    )
    return result.rowcount or 0


# -------------------------------------------------------------------------
# Hospitals  ← silver.hospitals  (tier: plan_hospital | other)
# -------------------------------------------------------------------------


def enrich_hospitals(conn: Connection) -> int:
    """Top 2 nearest hospitals."""
    result = conn.execute(
        text(
            """
            UPDATE listings_geo_context lgc
            SET nearest_hospital_name = nearest.name,
                nearest_hospital_m    = nearest.distance_m,
                hospitals_top2        = top2.blob,
                enriched_at    = now()
            FROM listings l
            LEFT JOIN LATERAL (
                SELECT h.name,
                       ST_Distance(h.geom::geography,
                                   l.location::geography)::int AS distance_m
                FROM hospitals h
                WHERE l.location IS NOT NULL
                ORDER BY h.geom <-> l.location
                LIMIT 1
            ) nearest ON true
            LEFT JOIN LATERAL (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'name',       t.name,
                        'tier',       t.tier,
                        'distance_m', t.distance_m
                    )
                    ORDER BY t.distance_m
                ) AS blob
                FROM (
                    SELECT h.name, h.tier,
                           ST_Distance(h.geom::geography,
                                       l.location::geography)::int AS distance_m
                    FROM hospitals h
                    WHERE l.location IS NOT NULL
                    ORDER BY h.geom <-> l.location
                    LIMIT 2
                ) t
            ) top2 ON true
            WHERE lgc.listing_id = l.id
            """
        )
    )
    return result.rowcount or 0


# -------------------------------------------------------------------------
# Water  ← silver.water_bodies
# -------------------------------------------------------------------------


def enrich_water(conn: Connection) -> int:
    """Nearest water body."""
    result = conn.execute(
        text(
            """
            UPDATE listings_geo_context lgc
            SET water = nearest.blob,
                enriched_at = now()
            FROM listings l
            LEFT JOIN LATERAL (
                SELECT jsonb_build_object(
                    'name',       w.name,
                    'water_kind', w.water_kind,
                    'distance_m', ST_Distance(w.geom::geography,
                                              l.location::geography)::int
                ) AS blob
                FROM water_bodies w
                WHERE l.location IS NOT NULL
                ORDER BY w.geom <-> l.location
                LIMIT 1
            ) nearest ON true
            WHERE lgc.listing_id = l.id
            """
        )
    )
    return result.rowcount or 0


# -------------------------------------------------------------------------
# Noise  ← silver.noise_levels
# -------------------------------------------------------------------------


def enrich_noise(conn: Connection) -> int:
    """Nearest noise sample (chip total_lden + detail breakdown)."""
    result = conn.execute(
        text(
            """
            UPDATE listings_geo_context lgc
            SET noise_total_lden = nearest.total_lden,
                noise_total_lnight = nearest.total_lnight,
                noise_profile    = nearest.blob,
                enriched_at      = now()
            FROM listings l
            LEFT JOIN LATERAL (
                SELECT n.noise_total_lden AS total_lden,
                       n.noise_total_lnight AS total_lnight,
                       jsonb_build_object(
                           'total_lden',  n.noise_total_lden,
                           'total_lnight', n.noise_total_lnight,
                           'street_lden', n.noise_street_lden,
                           'rail_lden',   n.noise_rail_lden,
                           'distance_m',  ST_Distance(n.geom::geography,
                                                      l.location::geography)::int
                       ) AS blob
                FROM noise_levels n
                WHERE l.location IS NOT NULL
                ORDER BY n.geom <-> l.location
                LIMIT 1
            ) nearest ON true
            WHERE lgc.listing_id = l.id
            """
        )
    )
    return result.rowcount or 0


# -------------------------------------------------------------------------
# Greenery  ← silver.parks + silver.playgrounds (composite)
# WHO Europe rule: 300m buffer, cemeteries weighted 0.5. Heavy
# (ST_Area∘ST_Intersection per intersecting feature), but only O(features
# within 300m) per listing — typically a handful.
# -------------------------------------------------------------------------


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


# -------------------------------------------------------------------------
# Density  ← silver.population_density_2025  (ST_Contains on LOR polygon)
# -------------------------------------------------------------------------


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


# -------------------------------------------------------------------------
# Disabled parking  ← silver.disabled_parking
# -------------------------------------------------------------------------


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
# Public toilets  ← silver.public_toilets
# -------------------------------------------------------------------------


def enrich_toilets(conn: Connection) -> int:
    """Nearest public toilet (chip) + top 3 detail blob."""
    result = conn.execute(
        text(
            """
            UPDATE listings_geo_context lgc
            SET nearest_toilet_m = nearest.distance_m,
                toilets_top3       = top3.blob,
                enriched_at        = now()
            FROM listings l
            LEFT JOIN LATERAL (
                SELECT pt.fid,
                       pt.location,
                       ST_Distance(pt.geom::geography,
                                   l.location::geography)::int AS distance_m
                FROM public_toilets pt
                WHERE l.location IS NOT NULL
                ORDER BY pt.geom <-> l.location
                LIMIT 1
            ) nearest ON true
            LEFT JOIN LATERAL (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'fid', t.fid,
                        'location', t.location,
                        'operator', t.operator,
                        'model_type', t.model_type,
                        'opening_hours', t.opening_hours,
                        'usage_fee', t.usage_fee,
                        'wheelchair_accessible', t.wheelchair_accessible,
                        'distance_m', t.distance_m
                    )
                    ORDER BY t.distance_m
                ) AS blob
                FROM (
                    SELECT pt.fid, pt.location, pt.operator, pt.model_type,
                           pt.opening_hours, pt.usage_fee,
                           pt.wheelchair_accessible,
                           ST_Distance(pt.geom::geography,
                                       l.location::geography)::int AS distance_m
                    FROM public_toilets pt
                    WHERE l.location IS NOT NULL
                    ORDER BY pt.geom <-> l.location
                    LIMIT 3
                ) t
            ) top3 ON true
            WHERE lgc.listing_id = l.id
            """
        )
    )
    return result.rowcount or 0


# -------------------------------------------------------------------------
# Trees  ← silver.trees
# -------------------------------------------------------------------------


def enrich_trees(conn: Connection) -> int:
    """Tree count within 100m."""
    result = conn.execute(
        text(
            """
            UPDATE listings_geo_context lgc
            SET trees_within_100_count = COALESCE(cnt.cnt, 0),
                enriched_at      = now()
            FROM listings l
            LEFT JOIN LATERAL (
                SELECT COUNT(*) AS cnt
                FROM trees t2
                WHERE l.location IS NOT NULL
                  AND ST_DWithin(t2.geom::geography, l.location::geography, :radius_100)
            ) cnt ON true
            WHERE lgc.listing_id = l.id
            """,
        ),
        {"radius_100": 100},
    )
    return result.rowcount or 0


def enrich_admin_areas(conn: Connection) -> int:
    """Assign listing Bezirk / Ortsteil from ALKIS boundary polygons."""
    result = conn.execute(
        text(
            """
            UPDATE listings_geo_context lgc
            SET listing_bezirk   = bez.name,
                listing_ortsteil = ort.name,
                enriched_at      = now()
            FROM listings l
            LEFT JOIN LATERAL (
                SELECT b.name
                FROM bezirke b
                WHERE l.location IS NOT NULL
                  AND ST_Covers(b.geom, l.location)
                ORDER BY ST_Area(b.geom) ASC
                LIMIT 1
            ) bez ON true
            LEFT JOIN LATERAL (
                SELECT o.name
                FROM ortsteile o
                WHERE l.location IS NOT NULL
                  AND ST_Covers(o.geom, l.location)
                ORDER BY ST_Area(o.geom) ASC
                LIMIT 1
            ) ort ON true
            WHERE lgc.listing_id = l.id
            """
        )
    )
    return result.rowcount or 0


# -------------------------------------------------------------------------
# Registry — used by `gold.run` to dispatch `--only` family filtering.
# -------------------------------------------------------------------------


CHIP_FAMILIES = {
    "transit": enrich_transit,
    "parks": enrich_parks,
    "playground": enrich_playground,
    "schools": enrich_schools,
    "kitas": enrich_kitas,
    "hospitals": enrich_hospitals,
    "water": enrich_water,
    "noise": enrich_noise,
    "greenery": enrich_greenery,
    "density": enrich_density,
    "disabled_parking": enrich_disabled_parking,
    "toilets": enrich_toilets,
    "trees": enrich_trees,
    "admin_areas": enrich_admin_areas,
    "buildings": enrich_buildings,
}
