"""curated places (universities) + a priority column on named_places

Revision ID: 0009_curated_places
Revises: 0008_transit_stops_gazetteer
Create Date: 2026-07-02

Forward-only, schema-only (new table + view recreate + indexes — no data
touched), so the round-trip test (`tests/integration/test_alembic_round_trip.py`)
stays meaningful. All DDL lands in the `world` schema via the env search_path
(unqualified, matching 0003–0008).

Adds an editorially-curated gazetteer arm for places the raw OSM/ALKIS
footprints can't resolve cleanly — universities. Their buildings are scattered
across many same-named `landmarks` rows (e.g. "Technische Universität Berlin"
×9), so `locate_place` today matches a random annex; and abbreviations ("TU",
"FU", "HU") fall below the pg_trgm similarity threshold entirely. The curated
rows carry a hand-authored (footprint-derived, frozen) campus geometry and lead
the candidate menu via a `priority` boost so the agent resolves to *the campus*.
HU is authored as three campuses (Mitte / Nord / Adlershof) so it stays a
"which one?" question — geometry alone can't separate them (Nord sits inside the
Mitte cluster). See `agent-compound-docs/decisions/named-place-search.md`.

Two changes:
  - `curated_places` table (+ GIST geom index + GIN pg_trgm name index mirroring
    the per-source indexes 0007 added). `curated_places` is populated by the
    geo-context loader (`_run_universities` in `geo_context/run.py`) from the
    frozen `university_seed.yaml`, NOT by this migration (schema/data split).
  - Recreate `world.named_places` with a curated arm AND a `priority` column on
    every arm (curated = 1, everything else = 0). `PlaceService.locate` orders
    `priority DESC` first, so curated rows outrank raw footprints of the same
    name. `src_id` stays TEXT (the 0008 invariant); the backend types the new
    column Integer in `listings/models.py`.

No ETL is triggered here; run `--profile geo-context` (or the standalone loader)
to populate `curated_places`.
"""

from __future__ import annotations

from alembic import op

# NB: ≤32 chars — `world.alembic_version.version_num` is varchar(32).
revision = "0009_curated_places"
down_revision = "0008_transit_stops_gazetteer"
branch_labels = None
depends_on = None


# The 0009 view: a curated arm + a `priority` column on every arm, plus an
# `anchor_geom` column. `anchor_geom` is the curated place's optional
# representative point (a campus's MAIN building) synthesized from the
# curated_places.anchor_lat/lon floats; NULL for every non-curated arm. The
# backend's `anchor_point` (the travel-time/distance lens anchor) uses
# COALESCE(anchor_geom, ST_Centroid(geom)) so a lens routes to the main
# building rather than the mean of a sprawling multi-building campus.
_NAMED_PLACES_V3 = """
CREATE VIEW world.named_places AS
    SELECT kind, id::text AS src_id, kind || ':' || id AS place_ref,
           name, description, geom, 1 AS priority,
           CASE WHEN anchor_lat IS NOT NULL
                THEN ST_SetSRID(ST_MakePoint(anchor_lon, anchor_lat), 4326)
           END AS anchor_geom
        FROM curated_places
    UNION ALL
    SELECT 'landmark', id::text, 'landmark:' || id,
           name, description, geom, 0, NULL::geometry FROM landmarks
    UNION ALL
    SELECT 'park', id::text, 'park:' || id, name, NULL::text, geom, 0,
           NULL::geometry FROM parks
    UNION ALL
    SELECT 'water', id::text, 'water:' || id, name, NULL::text, geom, 0,
           NULL::geometry FROM water_bodies
    UNION ALL
    SELECT 'school', id::text, 'school:' || id, name, NULL::text, geom, 0,
           NULL::geometry FROM schools
    UNION ALL
    SELECT 'kita', id::text, 'kita:' || id, name, NULL::text, geom, 0,
           NULL::geometry FROM kitas
    UNION ALL
    SELECT 'hospital', id::text, 'hospital:' || id, name, NULL::text, geom, 0,
           NULL::geometry FROM hospitals
    UNION ALL
    SELECT 'transit_stop', min(stop_id), 'transit_stop:' || min(stop_id),
           name, NULL::text, ST_Centroid(ST_Collect(geom)), 0, NULL::geometry
        FROM transit_stops GROUP BY name
"""

# The 0008 view: every arm casts src_id to text; no curated arm, no priority
# (for downgrade()).
_NAMED_PLACES_V2 = """
CREATE VIEW world.named_places AS
    SELECT 'landmark' AS kind, id::text AS src_id,
           'landmark:' || id AS place_ref, name, description, geom FROM landmarks
    UNION ALL
    SELECT 'park', id::text, 'park:' || id, name, NULL::text, geom FROM parks
    UNION ALL
    SELECT 'water', id::text, 'water:' || id, name, NULL::text, geom FROM water_bodies
    UNION ALL
    SELECT 'school', id::text, 'school:' || id, name, NULL::text, geom FROM schools
    UNION ALL
    SELECT 'kita', id::text, 'kita:' || id, name, NULL::text, geom FROM kitas
    UNION ALL
    SELECT 'hospital', id::text, 'hospital:' || id, name, NULL::text, geom
        FROM hospitals
    UNION ALL
    SELECT 'transit_stop', min(stop_id), 'transit_stop:' || min(stop_id),
           name, NULL::text, ST_Centroid(ST_Collect(geom))
        FROM transit_stops GROUP BY name
"""


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE curated_places (
            id           BIGSERIAL PRIMARY KEY,
            kind         TEXT NOT NULL DEFAULT 'university',
            name         TEXT NOT NULL,
            description  TEXT,
            geom         geometry(Geometry, 4326) NOT NULL,
            -- Optional representative point (e.g. a campus's MAIN building).
            -- Stored as plain floats so the geopandas loader writes them with
            -- no second-geometry-column handling; the named_places view
            -- materialises them into `anchor_geom`. NULL → lens falls back to
            -- the footprint centroid.
            anchor_lat   double precision,
            anchor_lon   double precision
        )
        """
    )
    op.execute(
        "CREATE INDEX curated_places_geom_gix ON curated_places USING GIST (geom)"
    )
    op.execute(
        "CREATE INDEX curated_places_name_trgm "
        "ON curated_places USING GIN (name gin_trgm_ops)"
    )
    # A view can't be ALTERed to add a column / a UNION arm — drop + recreate.
    op.execute("DROP VIEW IF EXISTS world.named_places")
    op.execute(_NAMED_PLACES_V3)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS world.named_places")
    op.execute(_NAMED_PLACES_V2)
    op.execute("DROP TABLE IF EXISTS curated_places")
