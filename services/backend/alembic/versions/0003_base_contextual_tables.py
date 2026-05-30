"""add gis context tables (slim edition)

Revision ID: 0003_base_contextual_tables
Revises: 0002_postgis_and_embedding_dim
Create Date: 2026-05-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# -------------------------------------------------------------------------
# Alembic migration graph metadata
# -------------------------------------------------------------------------

revision: str = "0003_base_contextual_tables"

down_revision: str | Sequence[str] | None = (
    "0002_postgis_and_embedding_dim"
)

branch_labels: str | Sequence[str] | None = None

depends_on: str | Sequence[str] | None = None


# -------------------------------------------------------------------------
# Forward migration
# -------------------------------------------------------------------------

def upgrade() -> None:

    # ---------------------------------------------------------------------
    # Berlin schools GIS context table (existing)
    # ---------------------------------------------------------------------

    op.execute(
        """
        CREATE TABLE schulen_schulen (

            id BIGSERIAL PRIMARY KEY,

            bsn TEXT,

            schulname TEXT,

            schulart TEXT,

            traeger TEXT,

            schultyp TEXT,

            bezirk TEXT,

            ortsteil TEXT,

            plz TEXT,

            strasse TEXT,

            hausnr TEXT,

            telefon TEXT,

            fax TEXT,

            email TEXT,

            internet TEXT,

            schuljahr TEXT,

            geom geometry(Point, 4326)

        )
        """
    )

    # ---------------------------------------------------------------------
    # Spatial GIS index (existing)
    # ---------------------------------------------------------------------

    op.execute(
        """
        CREATE INDEX schulen_schulen_geom_gix
        ON schulen_schulen
        USING GIST (geom)
        """
    )

    # ---------------------------------------------------------------------
    # Optional attribute indexes (existing)
    # Useful for filtering/search
    # ---------------------------------------------------------------------

    op.create_index(
        "idx_schulen_schulen_bezirk",
        "schulen_schulen",
        ["bezirk"],
    )

    op.create_index(
        "idx_schulen_schulen_ortsteil",
        "schulen_schulen",
        ["ortsteil"],
    )

    op.create_index(
        "idx_schulen_schulen_schulart",
        "schulen_schulen",
        ["schulart"],
    )

    # ---------------------------------------------------------------------
    # NEW TABLES BELOW
    # ---------------------------------------------------------------------
    # All names follow: <dataset>_<layer>
    # All geometries use SRID 4326 for consistency with schulen_schulen
    # ---------------------------------------------------------------------

    # =====================================================================
    # ua_einwohnerdichte_2025 : ua_einwohnerdichte_2025 (MULTIPOLYGON)
    # =====================================================================

    op.execute(
        """
        CREATE TABLE ua_einwohnerdichte_2025_ua_einwohnerdichte_2025 (

            id BIGSERIAL PRIMARY KEY,

            schluessel TEXT,

            ew2025 INTEGER,

            flalle DOUBLE PRECISION,

            ha DOUBLE PRECISION,

            ew_ha_2025 DOUBLE PRECISION,

            alter_u6 INTEGER,

            alter_6_u10 INTEGER,

            alter_10_u18 INTEGER,

            alter_18_u65 INTEGER,

            alter_65_u70 INTEGER,

            alter_70_u75 INTEGER,

            alter75_u80 INTEGER,

            alter_80plus INTEGER,

            typklar TEXT,

            etypklar TEXT,

            geom geometry(MultiPolygon, 4326)

        )
        """
    )

    op.execute(
        """
        CREATE INDEX ua_einwohnerdichte_2025_ua_einwohnerdichte_2025_geom_gix
        ON ua_einwohnerdichte_2025_ua_einwohnerdichte_2025
        USING GIST (geom)
        """
    )

    op.create_index(
        "idx_ua_einwdichte2025_schluessel",
        "ua_einwohnerdichte_2025_ua_einwohnerdichte_2025",
        ["schluessel"],
    )

    op.create_index(
        "idx_ua_einwdichte2025_typklar",
        "ua_einwohnerdichte_2025_ua_einwohnerdichte_2025",
        ["typklar"],
    )

    # =====================================================================
    # ua_einwohnerdichte_2025 : ua_einwohnerdichte_2025_entw (MULTIPOLYGON)
    # =====================================================================

    op.execute(
        """
        CREATE TABLE ua_einwohnerdichte_2025_ua_einwohnerdichte_2025_entw (

            id BIGSERIAL PRIMARY KEY,

            schluessel TEXT,

            ew2024 INTEGER,

            ew2025 INTEGER,

            flalle DOUBLE PRECISION,

            ha DOUBLE PRECISION,

            ew_ha_2024 DOUBLE PRECISION,

            ew_ha_2025 DOUBLE PRECISION,

            diff_2025_2024 INTEGER,

            typklar TEXT,

            etypklar TEXT,

            geom geometry(MultiPolygon, 4326)

        )
        """
    )

    op.execute(
        """
        CREATE INDEX ua_einwohnerdichte_2025_ua_einwohnerdichte_2025_entw_geom_gix
        ON ua_einwohnerdichte_2025_ua_einwohnerdichte_2025_entw
        USING GIST (geom)
        """
    )

    op.create_index(
        "idx_ua_einwdichte2025_entw_schluessel",
        "ua_einwohnerdichte_2025_ua_einwohnerdichte_2025_entw",
        ["schluessel"],
    )

    op.create_index(
        "idx_ua_einwdichte2025_entw_typklar",
        "ua_einwohnerdichte_2025_ua_einwohnerdichte_2025_entw",
        ["typklar"],
    )

    # =====================================================================
    # ua_stratlaerm_2022 : aa_fp_gesamt2022 (POINT)
    # =====================================================================

    op.execute(
        """
        CREATE TABLE ua_stratlaerm_2022_aa_fp_gesamt2022 (

            id BIGSERIAL PRIMARY KEY,

            importid TEXT,

            x DOUBLE PRECISION,

            y DOUBLE PRECISION,

            str_den DOUBLE PRECISION,

            str_n DOUBLE PRECISION,

            sch_den DOUBLE PRECISION,

            sch_n DOUBLE PRECISION,

            flg_den TEXT,

            flg_n TEXT,

            ges_den DOUBLE PRECISION,

            ges_n DOUBLE PRECISION,

            geom geometry(Point, 4326)

        )
        """
    )

    op.execute(
        """
        CREATE INDEX ua_stratlaerm_2022_aa_fp_gesamt2022_geom_gix
        ON ua_stratlaerm_2022_aa_fp_gesamt2022
        USING GIST (geom)
        """
    )

    op.create_index(
        "idx_ua_stratlaerm2022_importid",
        "ua_stratlaerm_2022_aa_fp_gesamt2022",
        ["importid"],
    )

    # =====================================================================
    # ua_gruenvolumen_2020 : a_gruenvol2020 (MULTIPOLYGON)
    # =====================================================================

    op.execute(
        """
        CREATE TABLE ua_gruenvolumen_2020_a_gruenvol2020 (

            id BIGSERIAL PRIMARY KEY,

            schluessel TEXT,

            schl5 TEXT,

            flalle DOUBLE PRECISION,

            woz TEXT,

            woz_name TEXT,

            grz TEXT,

            grz_name TEXT,

            typ TEXT,

            typklar TEXT,

            veghoh2020 DOUBLE PRECISION,

            vegproz2020 DOUBLE PRECISION,

            vegvola2010 DOUBLE PRECISION,

            vegvola2020 DOUBLE PRECISION,

            vegvol2010 DOUBLE PRECISION,

            vegvol2020 DOUBLE PRECISION,

            flubeb2020 DOUBLE PRECISION,

            veghoeubeb2020 DOUBLE PRECISION,

            vegproubeb2020 DOUBLE PRECISION,

            vegvolaube2020 DOUBLE PRECISION,

            vegvolubeb2020 DOUBLE PRECISION,

            changegvz DOUBLE PRECISION,

            ewoz_name TEXT,

            egrz_name TEXT,

            etypklar TEXT,

            geom geometry(MultiPolygon, 4326)

        )
        """
    )

    op.execute(
        """
        CREATE INDEX ua_gruenvolumen_2020_a_gruenvol2020_geom_gix
        ON ua_gruenvolumen_2020_a_gruenvol2020
        USING GIST (geom)
        """
    )

    op.create_index(
        "idx_ua_gruenvol2020_schluessel",
        "ua_gruenvolumen_2020_a_gruenvol2020",
        ["schluessel"],
    )

    op.create_index(
        "idx_ua_gruenvol2020_typklar",
        "ua_gruenvolumen_2020_a_gruenvol2020",
        ["typklar"],
    )

    # =====================================================================
    # gruenanlagen : gruenanlagen (MULTIPOLYGON)
    # =====================================================================

    op.execute(
        """
        CREATE TABLE gruenanlagen_gruenanlagen (

            id BIGSERIAL PRIMARY KEY,

            pitid TEXT,

            kennzeich TEXT,

            bezirkname TEXT,

            ortstlname TEXT,

            objartname TEXT,

            namenr TEXT,

            namezusatz TEXT,

            baujahr TEXT,

            sanierjahr TEXT,

            katasterfl DOUBLE PRECISION,

            widmung TEXT,

            plannr TEXT,

            planname TEXT,

            geom geometry(MultiPolygon, 4326)

        )
        """
    )

    op.execute(
        """
        CREATE INDEX gruenanlagen_gruenanlagen_geom_gix
        ON gruenanlagen_gruenanlagen
        USING GIST (geom)
        """
    )

    op.create_index(
        "idx_gruenanlagen_bezirkname",
        "gruenanlagen_gruenanlagen",
        ["bezirkname"],
    )

    op.create_index(
        "idx_gruenanlagen_ortstlname",
        "gruenanlagen_gruenanlagen",
        ["ortstlname"],
    )

    # =====================================================================
    # gruenanlagen : spielplaetze (MULTIPOLYGON)
    # =====================================================================

    op.execute(
        """
        CREATE TABLE gruenanlagen_spielplaetze (

            id BIGSERIAL PRIMARY KEY,

            pitid TEXT,

            kennzeich TEXT,

            bezirkname TEXT,

            ortstlname TEXT,

            objartname TEXT,

            namenr TEXT,

            namezusatz TEXT,

            baujahr TEXT,

            sanierjahr TEXT,

            katasterfl DOUBLE PRECISION,

            widmung TEXT,

            plannr TEXT,

            planname TEXT,

            nettospfl DOUBLE PRECISION,

            geom geometry(MultiPolygon, 4326)

        )
        """
    )

    op.execute(
        """
        CREATE INDEX gruenanlagen_spielplaetze_geom_gix
        ON gruenanlagen_spielplaetze
        USING GIST (geom)
        """
    )

    op.create_index(
        "idx_spielplaetze_bezirkname",
        "gruenanlagen_spielplaetze",
        ["bezirkname"],
    )

    op.create_index(
        "idx_spielplaetze_ortstlname",
        "gruenanlagen_spielplaetze",
        ["ortstlname"],
    )

    # =====================================================================
    # schulen : schulen_esb (MULTIPOLYGON)
    # =====================================================================

    op.execute(
        """
        CREATE TABLE schulen_schulen_esb (

            id BIGSERIAL PRIMARY KEY,

            esb TEXT,

            bez TEXT,

            bezname TEXT,

            geom geometry(MultiPolygon, 4326)

        )
        """
    )

    op.execute(
        """
        CREATE INDEX schulen_schulen_esb_geom_gix
        ON schulen_schulen_esb
        USING GIST (geom)
        """
    )

    op.create_index(
        "idx_schulen_esb_bez",
        "schulen_schulen_esb",
        ["bez"],
    )

    op.create_index(
        "idx_schulen_esb_bezname",
        "schulen_schulen_esb",
        ["bezname"],
    )

    # =====================================================================
    # krankenhaeuser : plankrankenhaeuser (POINT)
    # =====================================================================

    op.execute(
        """
        CREATE TABLE krankenhaeuser_plankrankenhaeuser (

            id BIGSERIAL PRIMARY KEY,

            gisid TEXT,

            nr_standort TEXT,

            kkh_standort TEXT,

            nr_kkh TEXT,

            kkh TEXT,

            gc_strasse TEXT,

            gc_haus TEXT,

            gc_plz TEXT,

            gc_ortsteil TEXT,

            betten_insgesamt INTEGER,

            geom geometry(Point, 4326)

        )
        """
    )

    op.execute(
        """
        CREATE INDEX krankenhaeuser_plankrankenhaeuser_geom_gix
        ON krankenhaeuser_plankrankenhaeuser
        USING GIST (geom)
        """
    )

    op.create_index(
        "idx_krankenhaeuser_plankrankenhaeuser_plz",
        "krankenhaeuser_plankrankenhaeuser",
        ["gc_plz"],
    )

    op.create_index(
        "idx_krankenhaeuser_plankrankenhaeuser_ortsteil",
        "krankenhaeuser_plankrankenhaeuser",
        ["gc_ortsteil"],
    )

    # =====================================================================
    # behindertenparkplaetze : bpark (POINT)
    # =====================================================================

    op.execute(
        """
        CREATE TABLE behindertenparkplaetze_bpark (

            id BIGSERIAL PRIMARY KEY,

            uid TEXT,

            bezirk TEXT,

            bezeichnun TEXT,

            bemerkung TEXT,

            anzahl INTEGER,

            polizei TEXT,

            standort TEXT,

            plz TEXT,

            ortsteil TEXT,

            gps_lat DOUBLE PRECISION,

            gps_lon DOUBLE PRECISION,

            datum DATE,

            geom geometry(Point, 4326)

        )
        """
    )

    op.execute(
        """
        CREATE INDEX behindertenparkplaetze_bpark_geom_gix
        ON behindertenparkplaetze_bpark
        USING GIST (geom)
        """
    )

    op.create_index(
        "idx_bpark_bezirk",
        "behindertenparkplaetze_bpark",
        ["bezirk"],
    )

    op.create_index(
        "idx_bpark_ortsteil",
        "behindertenparkplaetze_bpark",
        ["ortsteil"],
    )

    op.create_index(
        "idx_bpark_plz",
        "behindertenparkplaetze_bpark",
        ["plz"],
    )


# -------------------------------------------------------------------------
# Rollback migration
# -------------------------------------------------------------------------

def downgrade() -> None:

    # ---------------------------------------------------------------------
    # Drop indexes and tables in reverse dependency order
    # ---------------------------------------------------------------------

    # behindertenparkplaetze_bpark
    op.drop_index(
        "idx_bpark_plz",
        table_name="behindertenparkplaetze_bpark",
    )
    op.drop_index(
        "idx_bpark_ortsteil",
        table_name="behindertenparkplaetze_bpark",
    )
    op.drop_index(
        "idx_bpark_bezirk",
        table_name="behindertenparkplaetze_bpark",
    )
    op.execute(
        "DROP INDEX IF EXISTS behindertenparkplaetze_bpark_geom_gix"
    )
    op.execute(
        "DROP TABLE IF EXISTS behindertenparkplaetze_bpark"
    )

    # krankenhaeuser_plankrankenhaeuser
    op.drop_index(
        "idx_krankenhaeuser_plankrankenhaeuser_ortsteil",
        table_name="krankenhaeuser_plankrankenhaeuser",
    )
    op.drop_index(
        "idx_krankenhaeuser_plankrankenhaeuser_plz",
        table_name="krankenhaeuser_plankrankenhaeuser",
    )
    op.execute(
        "DROP INDEX IF EXISTS krankenhaeuser_plankrankenhaeuser_geom_gix"
    )
    op.execute(
        "DROP TABLE IF EXISTS krankenhaeuser_plankrankenhaeuser"
    )

    # schulen_schulen_esb
    op.drop_index(
        "idx_schulen_esb_bezname",
        table_name="schulen_schulen_esb",
    )
    op.drop_index(
        "idx_schulen_esb_bez",
        table_name="schulen_schulen_esb",
    )
    op.execute(
        "DROP INDEX IF EXISTS schulen_schulen_esb_geom_gix"
    )
    op.execute(
        "DROP TABLE IF EXISTS schulen_schulen_esb"
    )

    # gruenanlagen_spielplaetze
    op.drop_index(
        "idx_spielplaetze_ortstlname",
        table_name="gruenanlagen_spielplaetze",
    )
    op.drop_index(
        "idx_spielplaetze_bezirkname",
        table_name="gruenanlagen_spielplaetze",
    )
    op.execute(
        "DROP INDEX IF EXISTS gruenanlagen_spielplaetze_geom_gix"
    )
    op.execute(
        "DROP TABLE IF EXISTS gruenanlagen_spielplaetze"
    )

    # gruenanlagen_gruenanlagen
    op.drop_index(
        "idx_gruenanlagen_ortstlname",
        table_name="gruenanlagen_gruenanlagen",
    )
    op.drop_index(
        "idx_gruenanlagen_bezirkname",
        table_name="gruenanlagen_gruenanlagen",
    )
    op.execute(
        "DROP INDEX IF EXISTS gruenanlagen_gruenanlagen_geom_gix"
    )
    op.execute(
        "DROP TABLE IF EXISTS gruenanlagen_gruenanlagen"
    )

    # ua_gruenvolumen_2020_a_gruenvol2020
    op.drop_index(
        "idx_ua_gruenvol2020_typklar",
        table_name="ua_gruenvolumen_2020_a_gruenvol2020",
    )
    op.drop_index(
        "idx_ua_gruenvol2020_schluessel",
        table_name="ua_gruenvolumen_2020_a_gruenvol2020",
    )
    op.execute(
        "DROP INDEX IF EXISTS ua_gruenvolumen_2020_a_gruenvol2020_geom_gix"
    )
    op.execute(
        "DROP TABLE IF EXISTS ua_gruenvolumen_2020_a_gruenvol2020"
    )

    # ua_stratlaerm_2022_aa_fp_gesamt2022
    op.drop_index(
        "idx_ua_stratlaerm2022_importid",
        table_name="ua_stratlaerm_2022_aa_fp_gesamt2022",
    )
    op.execute(
        "DROP INDEX IF EXISTS ua_stratlaerm_2022_aa_fp_gesamt2022_geom_gix"
    )
    op.execute(
        "DROP TABLE IF EXISTS ua_stratlaerm_2022_aa_fp_gesamt2022"
    )

    # ua_einwohnerdichte_2025_ua_einwohnerdichte_2025_entw
    op.drop_index(
        "idx_ua_einwdichte2025_entw_typklar",
        table_name="ua_einwohnerdichte_2025_ua_einwohnerdichte_2025_entw",
    )
    op.drop_index(
        "idx_ua_einwdichte2025_entw_schluessel",
        table_name="ua_einwohnerdichte_2025_ua_einwohnerdichte_2025_entw",
    )
    op.execute(
        "DROP INDEX IF EXISTS ua_einwohnerdichte_2025_ua_einwohnerdichte_2025_entw_geom_gix"
    )
    op.execute(
        "DROP TABLE IF EXISTS ua_einwohnerdichte_2025_ua_einwohnerdichte_2025_entw"
    )

    # ua_einwohnerdichte_2025_ua_einwohnerdichte_2025
    op.drop_index(
        "idx_ua_einwdichte2025_typklar",
        table_name="ua_einwohnerdichte_2025_ua_einwohnerdichte_2025",
    )
    op.drop_index(
        "idx_ua_einwdichte2025_schluessel",
        table_name="ua_einwohnerdichte_2025_ua_einwohnerdichte_2025",
    )
    op.execute(
        "DROP INDEX IF EXISTS ua_einwohnerdichte_2025_ua_einwohnerdichte_2025_geom_gix"
    )
    op.execute(
        "DROP TABLE IF EXISTS ua_einwohnerdichte_2025_ua_einwohnerdichte_2025"
    )

    # Existing indexes/tables for schulen_schulen
    op.drop_index(
        "idx_schulen_schulen_schulart",
        table_name="schulen_schulen",
    )

    op.drop_index(
        "idx_schulen_schulen_ortsteil",
        table_name="schulen_schulen",
    )

    op.drop_index(
        "idx_schulen_schulen_bezirk",
        table_name="schulen_schulen",
    )

    op.execute(
        "DROP INDEX IF EXISTS schulen_schulen_geom_gix"
    )

    op.execute(
        "DROP TABLE IF EXISTS schulen_schulen"
    )
