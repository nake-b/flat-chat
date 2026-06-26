"""geo-context v2 — landmarks/kitas/admin polygons, ring, named_places view

Revision ID: 0007_geo_context_v2
Revises: 0006_spatial_junction_tables
Create Date: 2026-06-26

Forward-only, schema-only (no data repairs) so the round-trip test stays
meaningful. All DDL lands in the `world` schema via the env search_path
(unqualified, matching 0003–0006).

Adds the data foundation for the geo-context v2 feature set:

  - New silver source tables: `kitas` (Point), `landmarks` (mixed geometry —
    ALKIS named footprints + OSM Bauwerke, `source`+`category` discriminators),
    `bezirke` / `ortsteile` (admin polygons), `inner_city_zone` (the legal
    low-emission zone ≈ S-Bahn ring "Hundekopf", a single polygon → the
    "inside the ring" filter).
  - Renames `street_noise_2022` → `strategic_noise_2022` (it covers road +
    rail, not just streets). NOTE: the source table already carries
    `noise_total_lnight` (added in 0003 alongside `noise_total_lden`), so no
    column add is needed there — the Lnight column being added in this
    revision is the one on the gold `listings_geo_context` table.
  - Two new junction tables — `listings_nearby_kitas` /
    `listings_nearby_landmarks` — mirroring `listings_nearby_schools`.
  - Extends `listings_geo_context` with `inside_ring` / `listing_bezirk` /
    `listing_ortsteil` / `noise_total_lnight`, and DROPs the MSS columns
    (`mss_status` / `mss_dynamics` / `mss_profile`) — MSS is removed entirely.
  - Drops the now-unused `social_monitoring_2025` table.
  - GIST index on `landmarks(geom)`; GIN `pg_trgm` indexes on the `name`
    column of every named source table (`landmarks`, `parks`, `water_bodies`,
    `schools`, `kitas`, `hospitals`) for the `WHERE name % :q` search behind
    the `world.named_places` gazetteer view.
  - `world.named_places` VIEW — `UNION ALL` over the named source tables
    exposing `(kind, src_id, place_ref, name, description, geom)`. The view
    composes the opaque `place_ref` (`'park:'||id`) so the backend's
    `locate_place` never references the table list. See
    `agent-compound-docs/decisions/` (named-place search).

The `pg_trgm` EXTENSION is created by the postgres bootstrap
(`services/postgres/init/01-bootstrap.sql` + `scripts/bootstrap-schemas.sh`),
NOT here — this migration assumes it exists.

`downgrade()` cleanly reverses everything (re-adds the MSS columns + the
`social_monitoring_2025` table as empty shells; `gold.run` against the
pre-0007 code would refill them).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007_geo_context_v2"
down_revision: str | Sequence[str] | None = "0006_spatial_junction_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# -------------------------------------------------------------------------
# Forward migration
# -------------------------------------------------------------------------


def upgrade() -> None:
    # =====================================================================
    # kitas  ← Berlin GDI WFS: kitas / kitas  (Point)
    # =====================================================================
    op.execute(
        """
        CREATE TABLE kitas (
            id             BIGSERIAL PRIMARY KEY,
            name           TEXT,
            operator       TEXT,
            operator_type  TEXT,
            street         TEXT,
            house_number   TEXT,
            postal_code    TEXT,
            district       TEXT,
            neighborhood   TEXT,
            phone          TEXT,
            email          TEXT,
            website        TEXT,
            capacity       INTEGER,
            geom           geometry(Point, 4326)
        )
        """
    )
    op.execute("CREATE INDEX kitas_geom_gix ON kitas USING GIST (geom)")

    # =====================================================================
    # landmarks  ← ALKIS named building footprints (source='alkis') + OSM
    # Bauwerke (source='osm'). Mixed geometry — points (OSM nodes), lines
    # (bridges), polygons (footprints / stadium bowls).
    # =====================================================================
    op.execute(
        """
        CREATE TABLE landmarks (
            id           BIGSERIAL PRIMARY KEY,
            name         TEXT,
            description  TEXT,
            source       TEXT NOT NULL,
            category     TEXT NOT NULL,
            geom         geometry(Geometry, 4326)
        )
        """
    )
    op.execute("CREATE INDEX landmarks_geom_gix ON landmarks USING GIST (geom)")

    # =====================================================================
    # bezirke  ← ALKIS borough boundary polygons (MultiPolygon)
    # `name` holds the human label (Mitte, …); `bezirk_id` the numeric id.
    # =====================================================================
    op.execute(
        """
        CREATE TABLE bezirke (
            id         BIGSERIAL PRIMARY KEY,
            name       TEXT,
            bezirk_id  TEXT,
            geom       geometry(MultiPolygon, 4326)
        )
        """
    )
    op.execute("CREATE INDEX bezirke_geom_gix ON bezirke USING GIST (geom)")

    # =====================================================================
    # ortsteile  ← ALKIS locality boundary polygons (MultiPolygon)
    # =====================================================================
    op.execute(
        """
        CREATE TABLE ortsteile (
            id    BIGSERIAL PRIMARY KEY,
            name  TEXT,
            geom  geometry(MultiPolygon, 4326)
        )
        """
    )
    op.execute("CREATE INDEX ortsteile_geom_gix ON ortsteile USING GIST (geom)")

    # =====================================================================
    # inner_city_zone  ← Umweltzone low-emission polygon (single feature)
    # The "inside the ring" boundary. MultiPolygon for shape headroom.
    # =====================================================================
    op.execute(
        """
        CREATE TABLE inner_city_zone (
            id    BIGSERIAL PRIMARY KEY,
            name  TEXT,
            geom  geometry(MultiPolygon, 4326)
        )
        """
    )
    op.execute(
        "CREATE INDEX inner_city_zone_geom_gix "
        "ON inner_city_zone USING GIST (geom)"
    )

    # =====================================================================
    # Rename street_noise_2022 → strategic_noise_2022. The source already
    # carries noise_total_lnight (0003), so only the table + its index are
    # renamed here — no column add on the source.
    # =====================================================================
    op.execute("ALTER TABLE street_noise_2022 RENAME TO strategic_noise_2022")
    op.execute(
        "ALTER INDEX street_noise_2022_geom_gix "
        "RENAME TO strategic_noise_2022_geom_gix"
    )

    # =====================================================================
    # listings_nearby_kitas  ← kita junction (mirrors listings_nearby_schools)
    # =====================================================================
    op.execute(
        """
        CREATE TABLE listings_nearby_kitas (
            listing_id  UUID NOT NULL
                            REFERENCES listings(id) ON DELETE CASCADE,
            kita_id     TEXT NOT NULL,
            distance_m  INTEGER NOT NULL,
            name        TEXT,
            rank        SMALLINT NOT NULL,
            PRIMARY KEY (listing_id, kita_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_lnk_listing_distance "
        "ON listings_nearby_kitas (listing_id, distance_m)"
    )

    # =====================================================================
    # listings_nearby_landmarks  ← landmark junction (notable categories only)
    # =====================================================================
    op.execute(
        """
        CREATE TABLE listings_nearby_landmarks (
            listing_id   UUID NOT NULL
                             REFERENCES listings(id) ON DELETE CASCADE,
            landmark_id  TEXT NOT NULL,
            distance_m   INTEGER NOT NULL,
            category     TEXT,
            name         TEXT,
            rank         SMALLINT NOT NULL,
            PRIMARY KEY (listing_id, landmark_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_lnl_listing_distance "
        "ON listings_nearby_landmarks (listing_id, distance_m)"
    )
    op.execute(
        "CREATE INDEX ix_lnl_category "
        "ON listings_nearby_landmarks (category) "
        "WHERE category IS NOT NULL"
    )

    # =====================================================================
    # listings_geo_context — add v2 scalar columns, drop MSS.
    # =====================================================================
    op.execute("ALTER TABLE listings_geo_context ADD COLUMN inside_ring BOOLEAN")
    op.execute("ALTER TABLE listings_geo_context ADD COLUMN listing_bezirk TEXT")
    op.execute("ALTER TABLE listings_geo_context ADD COLUMN listing_ortsteil TEXT")
    op.execute(
        "ALTER TABLE listings_geo_context ADD COLUMN noise_total_lnight REAL"
    )
    op.execute("DROP INDEX IF EXISTS ix_lgc_mss_status")
    op.execute("DROP INDEX IF EXISTS ix_lgc_mss_dynamics")
    op.execute("ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS mss_status")
    op.execute("ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS mss_dynamics")
    op.execute("ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS mss_profile")

    # =====================================================================
    # Drop the now-unused MSS source table (MSS removed entirely).
    # =====================================================================
    op.execute("DROP TABLE IF EXISTS social_monitoring_2025")

    # =====================================================================
    # Trigram name indexes for the named_places gazetteer (`WHERE name % :q`).
    # The pg_trgm EXTENSION is created by the postgres bootstrap.
    # =====================================================================
    op.execute(
        "CREATE INDEX landmarks_name_trgm "
        "ON landmarks USING GIN (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX parks_name_trgm "
        "ON parks USING GIN (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX water_bodies_name_trgm "
        "ON water_bodies USING GIN (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX schools_name_trgm "
        "ON schools USING GIN (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX kitas_name_trgm "
        "ON kitas USING GIN (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX hospitals_name_trgm "
        "ON hospitals USING GIN (name gin_trgm_ops)"
    )

    # =====================================================================
    # world.named_places — the locate_place gazetteer view. UNION ALL over
    # the named source tables; the view owns place_ref composition + the
    # table↔kind mapping so the backend stays decoupled from the table list.
    # Every base table uses (id, name, geom); only landmarks carries a
    # `description`.
    # =====================================================================
    op.execute(
        """
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
    )


# -------------------------------------------------------------------------
# Rollback migration
# -------------------------------------------------------------------------


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS world.named_places")

    op.execute("DROP INDEX IF EXISTS hospitals_name_trgm")
    op.execute("DROP INDEX IF EXISTS kitas_name_trgm")
    op.execute("DROP INDEX IF EXISTS schools_name_trgm")
    op.execute("DROP INDEX IF EXISTS water_bodies_name_trgm")
    op.execute("DROP INDEX IF EXISTS parks_name_trgm")
    op.execute("DROP INDEX IF EXISTS landmarks_name_trgm")

    # Recreate the MSS source table as an empty shell (matches 0003 DDL) so
    # downgrade past 0007 leaves a schema the pre-0007 gold code can refill.
    op.execute(
        """
        CREATE TABLE social_monitoring_2025 (
            id                         BIGSERIAL PRIMARY KEY,
            planning_area_id           TEXT,
            planning_area_name         TEXT,
            district_id                TEXT,
            residents                  INTEGER,
            dynamics_index_score       INTEGER,
            dynamics_index_label       TEXT,
            social_inequality_category TEXT,
            social_inequality_score    INTEGER,
            social_inequality_label    TEXT,
            status_index_score         INTEGER,
            status_index_label         TEXT,
            year                       INTEGER,
            notes                      TEXT,
            geom                       geometry(MultiPolygon, 4326)
        )
        """
    )
    op.execute(
        "CREATE INDEX social_monitoring_2025_geom_gix "
        "ON social_monitoring_2025 USING GIST (geom)"
    )

    # Restore MSS columns on listings_geo_context + their indexes.
    op.execute(
        "ALTER TABLE listings_geo_context ADD COLUMN IF NOT EXISTS mss_status TEXT"
    )
    op.execute(
        "ALTER TABLE listings_geo_context ADD COLUMN IF NOT EXISTS mss_dynamics TEXT"
    )
    op.execute(
        "ALTER TABLE listings_geo_context ADD COLUMN IF NOT EXISTS mss_profile JSONB"
    )
    op.execute(
        "CREATE INDEX ix_lgc_mss_status "
        "ON listings_geo_context (mss_status) "
        "WHERE mss_status IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_lgc_mss_dynamics "
        "ON listings_geo_context (mss_dynamics) "
        "WHERE mss_dynamics IS NOT NULL"
    )

    # Drop the v2 scalar columns.
    op.execute(
        "ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS noise_total_lnight"
    )
    op.execute(
        "ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS listing_ortsteil"
    )
    op.execute(
        "ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS listing_bezirk"
    )
    op.execute(
        "ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS inside_ring"
    )

    # Drop the v2 junction tables.
    op.execute("DROP TABLE IF EXISTS listings_nearby_landmarks")
    op.execute("DROP TABLE IF EXISTS listings_nearby_kitas")

    # Reverse the noise rename (the source Lnight column predates 0007 — 0003
    # created it — so it is NOT dropped here).
    op.execute(
        "ALTER INDEX strategic_noise_2022_geom_gix "
        "RENAME TO street_noise_2022_geom_gix"
    )
    op.execute("ALTER TABLE strategic_noise_2022 RENAME TO street_noise_2022")

    # Drop the new silver source tables.
    op.execute("DROP TABLE IF EXISTS inner_city_zone")
    op.execute("DROP TABLE IF EXISTS ortsteile")
    op.execute("DROP TABLE IF EXISTS bezirke")
    op.execute("DROP TABLE IF EXISTS landmarks")
    op.execute("DROP TABLE IF EXISTS kitas")
