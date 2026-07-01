"""transit stops in the named_places gazetteer (B′)

Revision ID: 0008_transit_stops_gazetteer
Revises: 0007_geo_context_v2
Create Date: 2026-06-29

Forward-only, schema-only (view + index only — no data touched), so the
round-trip test (`tests/integration/test_alembic_round_trip.py`) stays
meaningful. All DDL lands in the `world` schema via the env search_path
(unqualified, matching 0003–0007).

Lets `locate_place` (and therefore `apply_travel_time`'s anchor) resolve
arbitrary S/U-Bahn/tram/bus stations — today they live only in
`world.transit_stops` and are invisible to the gazetteer. Two changes:

  - `transit_stops_name_trgm` GIN `pg_trgm` index on `transit_stops(name)`,
    mirroring the per-source-table trigram indexes 0007 added — this is what
    serves the `WHERE name % :q` fuzzy match behind `locate_place`.
  - Recreate `world.named_places` with a new arm for transit stops AND with
    `src_id` cast to TEXT on every arm. The text cast is forced by the new
    arm: a stop's `stop_id` is a colon-laden string (`de:11000:900100003`),
    so `UNION` needs one common `src_id` type — text. The backend moves with
    it (`listings/models.py` types the column Text; `_parse_place_ref` keeps
    the whole post-first-colon remainder as the id).

The transit arm de-duplicates the per-platform rows VBB ships (one row per
U2/U5/tram platform of "Alexanderplatz") into ONE station point via
`GROUP BY name` + the centroid of the collected platform geoms. `src_id` and
`place_ref` both use `min(stop_id)` over the group so they stay consistent
with how the backend re-queries the row by `src_id`. Trade-off: distinct
same-named stops in different places (a generic "Rathaus") collapse to one
centroid — acceptable for MVP.

No ETL re-run is needed: `transit_stops` is already populated; this only adds
a view + an index over existing data.
"""

from __future__ import annotations

from alembic import op

# NB: ≤32 chars — `world.alembic_version.version_num` is varchar(32).
revision = "0008_transit_stops_gazetteer"
down_revision = "0007_geo_context_v2"
branch_labels = None
depends_on = None


# The 0008 view: every arm casts src_id to text; a deduped transit_stop arm.
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

# The 0007 view: integer src_id, no transit arm (for downgrade()).
_NAMED_PLACES_V1 = """
CREATE VIEW world.named_places AS
    SELECT 'landmark' AS kind, id AS src_id, 'landmark:' || id AS place_ref,
           name, description, geom FROM landmarks
    UNION ALL
    SELECT 'park', id, 'park:' || id, name, NULL::text, geom FROM parks
    UNION ALL
    SELECT 'water', id, 'water:' || id, name, NULL::text, geom FROM water_bodies
    UNION ALL
    SELECT 'school', id, 'school:' || id, name, NULL::text, geom FROM schools
    UNION ALL
    SELECT 'kita', id, 'kita:' || id, name, NULL::text, geom FROM kitas
    UNION ALL
    SELECT 'hospital', id, 'hospital:' || id, name, NULL::text, geom FROM hospitals
"""


def upgrade() -> None:
    op.execute(
        "CREATE INDEX transit_stops_name_trgm "
        "ON transit_stops USING GIN (name gin_trgm_ops)"
    )
    # A view can't be ALTERed to change a column's type / add a UNION arm —
    # drop + recreate.
    op.execute("DROP VIEW IF EXISTS world.named_places")
    op.execute(_NAMED_PLACES_V2)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS world.named_places")
    op.execute(_NAMED_PLACES_V1)
    op.execute("DROP INDEX IF EXISTS transit_stops_name_trgm")
