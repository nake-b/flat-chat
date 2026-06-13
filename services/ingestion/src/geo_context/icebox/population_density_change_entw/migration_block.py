"""Ready-to-paste alembic migration block for the deferred population_density
change table. NOT IMPORTED ANYWHERE. See ../README.md."""

# Paste inside a `def upgrade():` body of a new alembic revision.
_UPGRADE_SQL = """
CREATE TABLE population_density_change_2024_2025 (
    id                          BIGSERIAL PRIMARY KEY,
    lor_key                     TEXT,
    population_2024             INTEGER,
    population_2025             INTEGER,
    area_total                  DOUBLE PRECISION,
    area_hectares               DOUBLE PRECISION,
    population_per_hectare_2024 DOUBLE PRECISION,
    population_per_hectare_2025 DOUBLE PRECISION,
    diff_2025_2024              INTEGER,
    area_type                   TEXT,
    area_type_en                TEXT,
    geom                        geometry(MultiPolygon, 4326)
);

CREATE INDEX population_density_change_2024_2025_geom_gix
    ON population_density_change_2024_2025 USING GIST (geom);
"""

# Paste inside the matching `def downgrade():` body.
_DOWNGRADE_SQL = """
DROP TABLE IF EXISTS population_density_change_2024_2025;
"""
