"""geo-context tables: Berlin GDI WFS + VBB GTFS

Revision ID: 0003_base_contextual_tables
Revises: 0002_postgis_and_embedding_dim
Create Date: 2026-05-29

Creates the silver-tier tables consumed by the geo_context ingestion
pipeline (`services/ingestion/src/geo_context/`). All column and table
names are English; the German→English rename happens in the Transform
stage (`geo_context/transform/aliases.py`) so this schema reflects the
final shape the chat agent queries.

Index policy: only GIST on geom (plus GIST on geom::geography for
transit_stops, mirroring the listings.location pattern in 0002).
Attribute b-tree indexes are intentionally omitted — they're easy to
add back when a real query needs one.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_base_contextual_tables"
down_revision: str | Sequence[str] | None = "0002_postgis_and_embedding_dim"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# -------------------------------------------------------------------------
# Forward migration
# -------------------------------------------------------------------------


def upgrade() -> None:
    # =====================================================================
    # schools  ← Berlin GDI WFS: schulen / schulen  (Point)
    # =====================================================================

    op.execute(
        """
        CREATE TABLE schools (
            id              BIGSERIAL PRIMARY KEY,
            school_number   TEXT,
            name            TEXT,
            school_type     TEXT,
            operator        TEXT,
            school_category TEXT,
            district        TEXT,
            neighborhood    TEXT,
            postal_code     TEXT,
            street          TEXT,
            house_number    TEXT,
            phone           TEXT,
            email           TEXT,
            website         TEXT,
            school_year     TEXT,
            geom            geometry(Point, 4326)
        )
        """
    )
    op.execute("CREATE INDEX schools_geom_gix ON schools USING GIST (geom)")

    # =====================================================================
    # school_catchments  ← schulen / schulen_esb  (MultiPolygon)
    # Primary-school catchment polygons (Einschulungsbereiche).
    # =====================================================================

    op.execute(
        """
        CREATE TABLE school_catchments (
            id              BIGSERIAL PRIMARY KEY,
            catchment_id    TEXT,
            school_number   TEXT,
            school_name     TEXT,
            geom            geometry(MultiPolygon, 4326)
        )
        """
    )
    op.execute(
        "CREATE INDEX school_catchments_geom_gix "
        "ON school_catchments USING GIST (geom)"
    )

    # =====================================================================
    # population_density_2025  ← ua_einwohnerdichte_2025 / same  (MultiPolygon)
    # =====================================================================

    op.execute(
        """
        CREATE TABLE population_density_2025 (
            id                      BIGSERIAL PRIMARY KEY,
            lor_key                 TEXT,
            population              INTEGER,
            area_total              DOUBLE PRECISION,
            area_hectares           DOUBLE PRECISION,
            population_per_hectare  DOUBLE PRECISION,
            age_under_6             INTEGER,
            age_6_to_10             INTEGER,
            age_10_to_18            INTEGER,
            age_18_to_65            INTEGER,
            age_65_to_70            INTEGER,
            age_70_to_75            INTEGER,
            age_75_to_80            INTEGER,
            age_80_plus             INTEGER,
            area_type               TEXT,
            area_type_en            TEXT,
            geom                    geometry(MultiPolygon, 4326)
        )
        """
    )
    op.execute(
        "CREATE INDEX population_density_2025_geom_gix "
        "ON population_density_2025 USING GIST (geom)"
    )

    # =====================================================================
    # noise_levels  ← ua_stratlaerm_2022 / aa_fp_gesamt2022  (Point)
    # Source publishes raw x/y in EPSG:25833 alongside geom; we drop them
    # because they're redundant with the projected Point geometry.
    # The *_den fields are EU Lden values (day-evening-night, dB);
    # *_n are Lnight values. Air-noise (flg_*) is published as a
    # categorical TEXT label by the source — we preserve that.
    # =====================================================================

    op.execute(
        """
        CREATE TABLE noise_levels (
            id                      BIGSERIAL PRIMARY KEY,
            import_id               TEXT,
            noise_street_lden       DOUBLE PRECISION,
            noise_street_lnight     DOUBLE PRECISION,
            noise_rail_lden         DOUBLE PRECISION,
            noise_rail_lnight       DOUBLE PRECISION,
            noise_air_lden_class    TEXT,
            noise_air_lnight_class  TEXT,
            noise_total_lden        DOUBLE PRECISION,
            noise_total_lnight      DOUBLE PRECISION,
            geom                    geometry(Point, 4326)
        )
        """
    )
    op.execute(
        "CREATE INDEX noise_levels_geom_gix "
        "ON noise_levels USING GIST (geom)"
    )

    # =====================================================================
    # green_volume_2020  ← ua_gruenvolumen_2020 / a_gruenvol2020  (MultiPolygon)
    # =====================================================================

    op.execute(
        """
        CREATE TABLE green_volume_2020 (
            id                                BIGSERIAL PRIMARY KEY,
            lor_key                           TEXT,
            area_key_5                        TEXT,
            area_total                        DOUBLE PRECISION,
            area_use_code                     TEXT,
            area_use_name                     TEXT,
            block_type_code                   TEXT,
            block_type_name                   TEXT,
            area_class_code                   TEXT,
            area_class_name                   TEXT,
            veg_height_2020                   DOUBLE PRECISION,
            veg_percent_2020                  DOUBLE PRECISION,
            veg_vol_per_area_2010             DOUBLE PRECISION,
            veg_vol_per_area_2020             DOUBLE PRECISION,
            veg_vol_2010                      DOUBLE PRECISION,
            veg_vol_2020                      DOUBLE PRECISION,
            built_area_2020                   DOUBLE PRECISION,
            veg_height_excl_built_2020        DOUBLE PRECISION,
            veg_percent_excl_built_2020       DOUBLE PRECISION,
            veg_vol_per_area_excl_built_2020  DOUBLE PRECISION,
            veg_vol_excl_built_2020           DOUBLE PRECISION,
            veg_vol_change                    DOUBLE PRECISION,
            area_use_name_en                  TEXT,
            block_type_name_en                TEXT,
            area_class_name_en                TEXT,
            geom                              geometry(MultiPolygon, 4326)
        )
        """
    )
    op.execute(
        "CREATE INDEX green_volume_2020_geom_gix "
        "ON green_volume_2020 USING GIST (geom)"
    )

    # =====================================================================
    # parks  ← gruenanlagen / gruenanlagen  (MultiPolygon)
    # =====================================================================

    op.execute(
        """
        CREATE TABLE parks (
            id                  BIGSERIAL PRIMARY KEY,
            pit_id              TEXT,
            marker              TEXT,
            district            TEXT,
            neighborhood        TEXT,
            object_type         TEXT,
            name                TEXT,
            name_addition       TEXT,
            year_built          TEXT,
            year_renovated      TEXT,
            cadastral_area_m2   DOUBLE PRECISION,
            dedication          TEXT,
            plan_number         TEXT,
            plan_name           TEXT,
            geom                geometry(MultiPolygon, 4326)
        )
        """
    )
    op.execute("CREATE INDEX parks_geom_gix ON parks USING GIST (geom)")

    # =====================================================================
    # playgrounds  ← gruenanlagen / spielplaetze  (MultiPolygon)
    # =====================================================================

    op.execute(
        """
        CREATE TABLE playgrounds (
            id                  BIGSERIAL PRIMARY KEY,
            pit_id              TEXT,
            marker              TEXT,
            district            TEXT,
            neighborhood        TEXT,
            object_type         TEXT,
            name                TEXT,
            name_addition       TEXT,
            year_built          TEXT,
            year_renovated      TEXT,
            cadastral_area_m2   DOUBLE PRECISION,
            dedication          TEXT,
            plan_number         TEXT,
            plan_name           TEXT,
            play_area_m2        DOUBLE PRECISION,
            geom                geometry(MultiPolygon, 4326)
        )
        """
    )
    op.execute(
        "CREATE INDEX playgrounds_geom_gix ON playgrounds USING GIST (geom)"
    )

    # =====================================================================
    # hospitals  ← krankenhaeuser / plankrankenhaeuser + weitere_krankenhaeuser
    # Single table, `tier` discriminates between the two source layers so the
    # agent can filter by importance (plan_hospital = the ~80 Krankenhausplan
    # facilities with full ERs; other = smaller clinics / specialist hospitals).
    # =====================================================================

    op.execute(
        """
        CREATE TABLE hospitals (
            id               BIGSERIAL PRIMARY KEY,
            tier             TEXT NOT NULL,
            gis_id           TEXT,
            name             TEXT,
            street           TEXT,
            house_number     TEXT,
            postal_code      TEXT,
            neighborhood     TEXT,
            total_beds       INTEGER,
            location_number  TEXT,
            location_name    TEXT,
            hospital_number  TEXT,
            departments      TEXT,
            geom             geometry(Point, 4326),
            CONSTRAINT hospitals_tier_check
                CHECK (tier IN ('plan_hospital', 'other'))
        )
        """
    )
    op.execute("CREATE INDEX hospitals_geom_gix ON hospitals USING GIST (geom)")

    # =====================================================================
    # disabled_parking  ← behindertenparkplaetze / bpark  (Point)
    # Source gps_lat/gps_lon dropped — redundant with projected geom.
    # =====================================================================

    op.execute(
        """
        CREATE TABLE disabled_parking (
            id                   BIGSERIAL PRIMARY KEY,
            uid                  TEXT,
            district             TEXT,
            label                TEXT,
            note                 TEXT,
            spot_count           INTEGER,
            police_jurisdiction  TEXT,
            location             TEXT,
            postal_code          TEXT,
            neighborhood         TEXT,
            recorded_date        DATE,
            geom                 geometry(Point, 4326)
        )
        """
    )
    op.execute(
        "CREATE INDEX disabled_parking_geom_gix "
        "ON disabled_parking USING GIST (geom)"
    )

    # =====================================================================
    # public_toilets  ← toiletten / toiletten  (Point)
    # =====================================================================

    op.execute(
        """
        CREATE TABLE public_toilets (
            id                     BIGSERIAL PRIMARY KEY,
            fid                    TEXT,
            district               TEXT,
            location               TEXT,
            contract               TEXT,
            operator               TEXT,
            model_type             TEXT,
            symbol                 TEXT,
            opening_hours          TEXT,
            usage_fee              TEXT,
            payment_type           TEXT,
            wheelchair_accessible  TEXT,
            low_barrier            TEXT,
            changing_table         TEXT,
            geom                   geometry(Point, 4326)
        )
        """
    )
    op.execute(
        "CREATE INDEX public_toilets_geom_gix "
        "ON public_toilets USING GIST (geom)"
    )

    # =====================================================================
    # trees  ← baumbestand / anlagenbaeume + strassenbaeume  (Point)
    # `tree_type` discriminates park/facility trees from street trees.
    # =====================================================================

    op.execute(
        """
        CREATE TABLE trees (
            id                       BIGSERIAL PRIMARY KEY,
            tree_type                TEXT NOT NULL,
            gis_id                   TEXT,
            pit_id                   TEXT,
            tree_number              TEXT,
            object_number            TEXT,
            object_name              TEXT,
            species_de               TEXT,
            species_botanical        TEXT,
            genus_de                 TEXT,
            genus                    TEXT,
            species_group            TEXT,
            street_number            TEXT,
            street_name              TEXT,
            house_number             TEXT,
            house_number_suffix      TEXT,
            planting_year            INTEGER,
            age_years                INTEGER,
            crown_diameter_m         DOUBLE PRECISION,
            trunk_circumference_cm   DOUBLE PRECISION,
            height_m                 DOUBLE PRECISION,
            owner                    TEXT,
            district                 TEXT,
            geom                     geometry(Point, 4326),
            CONSTRAINT trees_tree_type_check
                CHECK (tree_type IN ('park', 'street'))
        )
        """
    )
    op.execute("CREATE INDEX trees_geom_gix ON trees USING GIST (geom)")

    # water_bodies  ← gewaesserkarte / e_gew_gewaesser_fl  (mixed geometry)
    # Surface representations for every Berlin water body: lakes (Wannsee,
    # Müggelsee), the Spree, Havel, canals. The source mixes Polygon,
    # MultiPolygon and GeometryCollection rows (some water bodies bundle
    # bank lines with surfaces), so the column accepts any geometry type.
    # =====================================================================

    op.execute(
        """
        CREATE TABLE water_bodies (
            id                   BIGSERIAL PRIMARY KEY,
            water_number_old     TEXT,
            water_type           TEXT,
            name                 TEXT,
            water_number_new     TEXT,
            district             TEXT,
            neighborhood         TEXT,
            receiving_water      TEXT,
            surface_area_m2      TEXT,
            length_m             TEXT,
            owner                TEXT,
            maintenance          TEXT,
            water_kind           TEXT,
            water_class          TEXT,
            notes                TEXT,
            geom                 geometry(Geometry, 4326)
        )
        """
    )
    op.execute(
        "CREATE INDEX water_bodies_geom_gix ON water_bodies USING GIST (geom)"
    )

    # =====================================================================
    # transit_stops  ← VBB GTFS stops + derived modes_served / lines_served
    # Platform children collapsed onto parent_station where present.
    # modes_served uses GTFS *Extended* Route Types (VBB's convention):
    #   100=mainline, 106=regional, 109=S-Bahn, 400=U-Bahn,
    #   700=bus, 900=tram, 1000=ferry, 3=legacy-bus.
    # See services/ingestion/src/geo_context/README.md for the full table.
    # =====================================================================

    op.execute(
        """
        CREATE TABLE transit_stops (
            stop_id              TEXT PRIMARY KEY,
            name                 TEXT NOT NULL,
            geom                 geometry(Point, 4326) NOT NULL,
            modes_served         SMALLINT[] NOT NULL,
            lines_served         TEXT[]     NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX transit_stops_geom_gix "
        "ON transit_stops USING GIST (geom)"
    )
    # Mirrors the listings.location_geog_idx pattern from 0002 so that the
    # agent's ST_DWithin(stop.geom::geography, listing.location::geography, m)
    # queries hit an index.
    op.execute(
        "CREATE INDEX transit_stops_geog_gix "
        "ON transit_stops USING GIST ((geom::geography))"
    )

    # =====================================================================
    # transit_routes  ← VBB GTFS routes.txt  (no geometry)
    # =====================================================================

    op.execute(
        """
        CREATE TABLE transit_routes (
            route_id    TEXT PRIMARY KEY,
            short_name  TEXT,
            long_name   TEXT,
            route_type  SMALLINT NOT NULL,
            color       TEXT,
            text_color  TEXT
        )
        """
    )

    # =====================================================================
    # transit_route_shapes  ← VBB GTFS shapes.txt collapsed
    # One canonical LineString per (route_id, direction_id), picked from the
    # most-frequently-used shape for that direction.
    # =====================================================================

    op.execute(
        """
        CREATE TABLE transit_route_shapes (
            route_id      TEXT NOT NULL REFERENCES transit_routes(route_id),
            direction_id  SMALLINT NOT NULL,
            geom          geometry(LineString, 4326) NOT NULL,
            PRIMARY KEY (route_id, direction_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX transit_route_shapes_geom_gix "
        "ON transit_route_shapes USING GIST (geom)"
    )


# -------------------------------------------------------------------------
# Rollback migration
# -------------------------------------------------------------------------


def downgrade() -> None:
    # Drop in reverse dependency order — children before parents.
    op.execute("DROP TABLE IF EXISTS transit_route_shapes")
    op.execute("DROP TABLE IF EXISTS transit_routes")
    op.execute("DROP TABLE IF EXISTS transit_stops")
    op.execute("DROP TABLE IF EXISTS water_bodies")
    op.execute("DROP TABLE IF EXISTS trees")
    op.execute("DROP TABLE IF EXISTS public_toilets")
    op.execute("DROP TABLE IF EXISTS disabled_parking")
    op.execute("DROP TABLE IF EXISTS hospitals")
    op.execute("DROP TABLE IF EXISTS playgrounds")
    op.execute("DROP TABLE IF EXISTS parks")
    op.execute("DROP TABLE IF EXISTS green_volume_2020")
    op.execute("DROP TABLE IF EXISTS noise_levels")
    op.execute("DROP TABLE IF EXISTS population_density_2025")
    op.execute("DROP TABLE IF EXISTS school_catchments")
    op.execute("DROP TABLE IF EXISTS schools")
    # PostGIS extension intentionally left installed.
