"""add current geo-context extensions (admin areas, landmarks, kitas, trees)

Revision ID: 007
Revises: 006
Create Date: 2026-06-22
"""

from collections.abc import Sequence

from alembic import op

revision: str = "007"
down_revision: str | Sequence[str] | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.street_noise_2022') IS NOT NULL
               AND to_regclass('public.noise_levels') IS NULL THEN
                ALTER TABLE street_noise_2022 RENAME TO noise_levels;
            END IF;
        END $$;
        """
    )
    op.execute("DROP INDEX IF EXISTS street_noise_2022_geom_gix")
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.noise_levels') IS NOT NULL THEN
                EXECUTE 'CREATE INDEX IF NOT EXISTS noise_levels_geom_gix ON noise_levels USING GIST (geom)';
            END IF;
        END $$;
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS buildings (
            id            BIGSERIAL PRIMARY KEY,
            name          TEXT,
            description   TEXT,
            street_name   TEXT,
            house_number  TEXT,
            pseudo_number TEXT,
            area_m2       DOUBLE PRECISION,
            num_storeys   INTEGER,
            geom          geometry(MultiPolygon, 4326)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS buildings_geom_gix ON buildings USING GIST (geom)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_buildings_name ON buildings (name)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS kitas (
            id           BIGSERIAL PRIMARY KEY,
            name         TEXT,
            operator     TEXT,
            street       TEXT,
            house_number TEXT,
            postal_code  TEXT,
            district     TEXT,
            neighborhood TEXT,
            phone        TEXT,
            email        TEXT,
            website      TEXT,
            address      TEXT,
            geom         geometry(Point, 4326)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS kitas_geom_gix ON kitas USING GIST (geom)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS bezirke (
            id    BIGSERIAL PRIMARY KEY,
            name  TEXT,
            geom  geometry(MultiPolygon, 4326)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS bezirke_geom_gix ON bezirke USING GIST (geom)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ortsteile (
            id    BIGSERIAL PRIMARY KEY,
            name  TEXT,
            geom  geometry(MultiPolygon, 4326)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ortsteile_geom_gix ON ortsteile USING GIST (geom)")

    op.execute(
        "ALTER TABLE listings_geo_context ADD COLUMN IF NOT EXISTS nearest_toilet_m INTEGER"
    )
    op.execute(
        "ALTER TABLE listings_geo_context ADD COLUMN IF NOT EXISTS toilets_top3 JSONB"
    )
    op.execute(
        "ALTER TABLE listings_geo_context ADD COLUMN IF NOT EXISTS nearest_hospital_m INTEGER"
    )
    op.execute(
        "ALTER TABLE listings_geo_context ADD COLUMN IF NOT EXISTS nearest_hospital_name TEXT"
    )
    op.execute(
        "ALTER TABLE listings_geo_context ADD COLUMN IF NOT EXISTS trees_within_100_count INTEGER"
    )
    op.execute(
        "ALTER TABLE listings_geo_context ADD COLUMN IF NOT EXISTS listing_bezirk TEXT"
    )
    op.execute(
        "ALTER TABLE listings_geo_context ADD COLUMN IF NOT EXISTS listing_ortsteil TEXT"
    )
    op.execute(
        "ALTER TABLE listings_geo_context ADD COLUMN IF NOT EXISTS nearest_landmark_m INTEGER"
    )
    op.execute(
        "ALTER TABLE listings_geo_context ADD COLUMN IF NOT EXISTS nearest_kita_m INTEGER"
    )
    op.execute(
        "ALTER TABLE listings_geo_context ADD COLUMN IF NOT EXISTS kitas_top3 JSONB"
    )
    op.execute(
        "ALTER TABLE listings_geo_context ADD COLUMN IF NOT EXISTS kitas_within_500_count INTEGER"
    )
    op.execute(
        "ALTER TABLE listings_geo_context ADD COLUMN IF NOT EXISTS landmarks_top3 JSONB"
    )
    op.execute(
        "ALTER TABLE listings_geo_context ADD COLUMN IF NOT EXISTS noise_total_lnight REAL"
    )

    # Remove superseded legacy columns.
    op.execute("ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS nearest_tree_m")
    op.execute("ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS trees_top3")
    op.execute("ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS trees_within_50_count")
    op.execute("ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS trees_species_within_100")
    op.execute("ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS kitas_top5")

    op.execute("DROP INDEX IF EXISTS ix_lgc_trees_species_within_100")
    op.execute("DROP INDEX IF EXISTS ix_lgc_trees_within_50_count")
    op.execute("DROP INDEX IF EXISTS ix_lgc_nearest_kita_m")
    op.execute("DROP INDEX IF EXISTS ix_lgc_nearest_landmark_m")
    op.execute("DROP INDEX IF EXISTS ix_lgc_landmarks_top3")

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_lgc_nearest_hospital_m "
        "ON listings_geo_context (nearest_hospital_m) "
        "WHERE nearest_hospital_m IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_lgc_trees_within_100_count "
        "ON listings_geo_context (trees_within_100_count) "
        "WHERE trees_within_100_count IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_lgc_kitas_within_500_count "
        "ON listings_geo_context (kitas_within_500_count) "
        "WHERE kitas_within_500_count IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_lgc_nearest_toilet_m "
        "ON listings_geo_context (nearest_toilet_m) "
        "WHERE nearest_toilet_m IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_lgc_nearest_landmark_m "
        "ON listings_geo_context (nearest_landmark_m) "
        "WHERE nearest_landmark_m IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_lgc_landmarks_top3 "
        "ON listings_geo_context USING GIN (landmarks_top3)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_lgc_nearest_kita_m "
        "ON listings_geo_context (nearest_kita_m) "
        "WHERE nearest_kita_m IS NOT NULL"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS noise_total_lnight"
    )

    op.execute("DROP INDEX IF EXISTS noise_levels_geom_gix")
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.noise_levels') IS NOT NULL
               AND to_regclass('public.street_noise_2022') IS NULL THEN
                ALTER TABLE noise_levels RENAME TO street_noise_2022;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.street_noise_2022') IS NOT NULL THEN
                EXECUTE 'CREATE INDEX IF NOT EXISTS street_noise_2022_geom_gix ON street_noise_2022 USING GIST (geom)';
            END IF;
        END $$;
        """
    )

    op.execute("DROP INDEX IF EXISTS ix_lgc_kitas_within_500_count")
    op.execute("DROP INDEX IF EXISTS ix_lgc_trees_within_100_count")
    op.execute("DROP INDEX IF EXISTS ix_lgc_nearest_hospital_m")
    op.execute("DROP INDEX IF EXISTS ix_lgc_nearest_kita_m")
    op.execute("DROP INDEX IF EXISTS ix_lgc_landmarks_top3")
    op.execute("DROP INDEX IF EXISTS ix_lgc_nearest_landmark_m")
    op.execute("DROP INDEX IF EXISTS ix_lgc_nearest_toilet_m")

    op.execute(
        "ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS kitas_within_500_count"
    )
    op.execute("ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS kitas_top3")
    op.execute(
        "ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS nearest_kita_m"
    )
    op.execute(
        "ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS listing_ortsteil"
    )
    op.execute(
        "ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS listing_bezirk"
    )
    op.execute(
        "ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS trees_within_100_count"
    )
    op.execute(
        "ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS nearest_hospital_name"
    )
    op.execute(
        "ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS nearest_hospital_m"
    )
    op.execute("ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS landmarks_top3")
    op.execute(
        "ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS nearest_landmark_m"
    )
    op.execute("ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS toilets_top3")
    op.execute(
        "ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS nearest_toilet_m"
    )

    op.execute("DROP INDEX IF EXISTS ortsteile_geom_gix")
    op.execute("DROP TABLE IF EXISTS ortsteile")
    op.execute("DROP INDEX IF EXISTS bezirke_geom_gix")
    op.execute("DROP TABLE IF EXISTS bezirke")
    op.execute("DROP INDEX IF EXISTS kitas_geom_gix")
    op.execute("DROP TABLE IF EXISTS kitas")
    op.execute("DROP INDEX IF EXISTS ix_buildings_name")
    op.execute("DROP INDEX IF EXISTS buildings_geom_gix")
    op.execute("DROP TABLE IF EXISTS buildings")
