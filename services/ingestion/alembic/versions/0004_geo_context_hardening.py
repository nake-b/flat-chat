"""geo-context hardening: drop dead columns, repair invalid geometries

Revision ID: 0004_geo_context_hardening
Revises: 0003_base_contextual_tables
Create Date: 2026-06-13

Follow-up cleanup after the first end-to-end geo_context ETL run:

- `transit_stops.wheelchair_boarding` is dropped. VBB's GTFS feed
  populates this field for ~3% of stops (all with value 1=accessible)
  and leaves the rest null, so the column carries no usable signal.

- `schools.fax` is dropped. Fax numbers don't belong in a 2026 product.

- Two source rows ship self-intersecting polygons
  (`green_volume_2020.id=629`, `water_bodies.Müggelspree`). The transformer
  now calls shapely's `make_valid` so future ETL runs land clean, but
  existing rows are repaired here for installs that don't immediately
  rerun the pipeline.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_geo_context_hardening"
down_revision: str | Sequence[str] | None = "0003_base_contextual_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE transit_stops DROP COLUMN IF EXISTS wheelchair_boarding")
    op.execute("ALTER TABLE schools DROP COLUMN IF EXISTS fax")

    # ST_MakeValid is a no-op for already-valid geometries — safe to run
    # over the whole table; the WHERE clause keeps the rewrite cheap.
    op.execute(
        "UPDATE green_volume_2020 SET geom = ST_MakeValid(geom) "
        "WHERE NOT ST_IsValid(geom)"
    )
    op.execute(
        "UPDATE water_bodies SET geom = ST_MakeValid(geom) "
        "WHERE NOT ST_IsValid(geom)"
    )


def downgrade() -> None:
    # Bring the columns back as nullable so a downgrade doesn't crash the
    # silver tier; values are not restored because the data was never
    # useful to begin with.
    op.execute("ALTER TABLE schools ADD COLUMN IF NOT EXISTS fax TEXT")
    op.execute(
        "ALTER TABLE transit_stops ADD COLUMN IF NOT EXISTS wheelchair_boarding SMALLINT"
    )
    # Geometry repair is not reversible — leave the cleaned geoms.
