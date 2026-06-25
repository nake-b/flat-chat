"""German→English column maps per (dataset, layer).

Each map is the source column name (as published by the Berlin GDI WFS)
keyed against the silver-table column it lands in. The migration creates
the silver tables with the English names; this map is what the
Transform step uses to bridge source → silver.

Columns that appear on the source but not in the map are dropped during
transform — keep ALIASES exhaustive for fields we want, omit fields we
don't.
"""

from __future__ import annotations

# (dataset, layer) -> {source_col: silver_col}
ALIASES: dict[tuple[str, str], dict[str, str]] = {
    # ---------------------------------------------------------------------
    # schulen  (Berliner Schulverzeichnis)
    # ---------------------------------------------------------------------
    ("schulen", "schulen"): {
        "bsn": "school_number",      # Berliner Schulnummer
        "schulname": "name",
        "schulart": "school_type",
        "traeger": "operator",
        "schultyp": "school_category",
        "bezirk": "district",
        "ortsteil": "neighborhood",
        "plz": "postal_code",
        "strasse": "street",
        "hausnr": "house_number",
        "telefon": "phone",
        "email": "email",
        "internet": "website",
        "schuljahr": "school_year",
    },
    ("schulen", "schulen_esb"): {
        "esb": "catchment_id",
        "bez": "school_number",
        "bezname": "school_name",
    },

    # ---------------------------------------------------------------------
    # ua_einwohnerdichte_2025  (Einwohnerdichte – population density)
    # ---------------------------------------------------------------------
    ("ua_einwohnerdichte_2025", "ua_einwohnerdichte_2025"): {
        "schluessel": "lor_key",
        "ew2025": "population",
        "flalle": "area_total",
        "ha": "area_hectares",
        "ew_ha_2025": "population_per_hectare",
        "alter_u6": "age_under_6",
        "alter_6_u10": "age_6_to_10",
        "alter_10_u18": "age_10_to_18",
        "alter_18_u65": "age_18_to_65",
        "alter_65_u70": "age_65_to_70",
        "alter_70_u75": "age_70_to_75",
        "alter75_u80": "age_75_to_80",
        "alter_80plus": "age_80_plus",
        "typklar": "area_type",
        "etypklar": "area_type_en",
    },

    # ---------------------------------------------------------------------
    # ua_stratlaerm_2022  (strategic noise map)
    # Source x/y columns are explicitly dropped — redundant with Point geom.
    # *_den is Lden (day-evening-night), *_n is Lnight (EU noise indicators).
    # Air-noise is published as TEXT class labels in the source.
    # ---------------------------------------------------------------------
    ("ua_stratlaerm_2022", "aa_fp_gesamt2022"): {
        "importid": "import_id",
        "str_den": "noise_street_lden",
        "str_n": "noise_street_lnight",
        "sch_den": "noise_rail_lden",
        "sch_n": "noise_rail_lnight",
        "flg_den": "noise_air_lden_class",
        "flg_n": "noise_air_lnight_class",
        "ges_den": "noise_total_lden",
        "ges_n": "noise_total_lnight",
    },

    # ---------------------------------------------------------------------
    # ua_gruenvolumen_2020  (3D vegetation volume from LiDAR)
    # ---------------------------------------------------------------------
    ("ua_gruenvolumen_2020", "a_gruenvol2020"): {
        "schluessel": "lor_key",
        "schl5": "area_key_5",
        "flalle": "area_total",
        "woz": "area_use_code",
        "woz_name": "area_use_name",
        "grz": "block_type_code",
        "grz_name": "block_type_name",
        "typ": "area_class_code",
        "typklar": "area_class_name",
        "veghoh2020": "veg_height_2020",
        "vegproz2020": "veg_percent_2020",
        "vegvola2010": "veg_vol_per_area_2010",
        "vegvola2020": "veg_vol_per_area_2020",
        "vegvol2010": "veg_vol_2010",
        "vegvol2020": "veg_vol_2020",
        "flubeb2020": "built_area_2020",
        "veghoeubeb2020": "veg_height_excl_built_2020",
        "vegproubeb2020": "veg_percent_excl_built_2020",
        "vegvolaube2020": "veg_vol_per_area_excl_built_2020",
        "vegvolubeb2020": "veg_vol_excl_built_2020",
        "changegvz": "veg_vol_change",
        "ewoz_name": "area_use_name_en",
        "egrz_name": "block_type_name_en",
        "etypklar": "area_class_name_en",
    },

    # ---------------------------------------------------------------------
    # gruenanlagen  (parks + playgrounds share most of the schema)
    # ---------------------------------------------------------------------
    ("gruenanlagen", "gruenanlagen"): {
        "pitid": "pit_id",
        "kennzeich": "marker",
        "bezirkname": "district",
        "ortstlname": "neighborhood",
        "objartname": "object_type",
        "namenr": "name",
        "namezusatz": "name_addition",
        "baujahr": "year_built",
        "sanierjahr": "year_renovated",
        "katasterfl": "cadastral_area_m2",
        "widmung": "dedication",
        "plannr": "plan_number",
        "planname": "plan_name",
    },
    ("gruenanlagen", "spielplaetze"): {
        "pitid": "pit_id",
        "kennzeich": "marker",
        "bezirkname": "district",
        "ortstlname": "neighborhood",
        "objartname": "object_type",
        "namenr": "name",
        "namezusatz": "name_addition",
        "baujahr": "year_built",
        "sanierjahr": "year_renovated",
        "katasterfl": "cadastral_area_m2",
        "widmung": "dedication",
        "plannr": "plan_number",
        "planname": "plan_name",
        "nettospfl": "play_area_m2",
    },

    # ---------------------------------------------------------------------
    # krankenhaeuser  (both layers feed one `hospitals` table; the `tier`
    # column is set by the orchestrator from the YAML `extra.tier` field).
    # ---------------------------------------------------------------------
    ("krankenhaeuser", "plankrankenhaeuser"): {
        "gisid": "gis_id",
        "kkh": "name",
        "gc_strasse": "street",
        "gc_haus": "house_number",
        "gc_plz": "postal_code",
        "gc_ortsteil": "neighborhood",
        "betten_insgesamt": "total_beds",
        "nr_standort": "location_number",
        "kkh_standort": "location_name",
        "nr_kkh": "hospital_number",
    },
    ("krankenhaeuser", "weitere_krankenhaeuser"): {
        "gisid": "gis_id",
        "name": "name",
        "gc_strasse": "street",
        "gc_haus": "house_number",
        "gc_plz": "postal_code",
        "gc_ortsteil": "neighborhood",
        "betten": "total_beds",
        "fachabteilungen": "departments",
    },

    # ---------------------------------------------------------------------
    # behindertenparkplaetze  (disabled parking)
    # Source gps_lat/gps_lon dropped — redundant with the projected geom.
    # ---------------------------------------------------------------------
    ("behindertenparkplaetze", "bpark"): {
        "uid": "uid",
        "bezirk": "district",
        "bezeichnun": "label",
        "bemerkung": "note",
        "anzahl": "spot_count",
        "polizei": "police_jurisdiction",
        "standort": "location",
        "plz": "postal_code",
        "ortsteil": "neighborhood",
        "datum": "recorded_date",
    },

    # ---------------------------------------------------------------------
    # toiletten  (public toilets)
    # ---------------------------------------------------------------------
    ("toiletten", "toiletten"): {
        "fid": "fid",
        "bezirk": "district",
        "standort": "location",
        "vertrag": "contract",
        "betreiber": "operator",
        "modelltyp": "model_type",
        "symbol": "symbol",
        "oeffnungszeiten": "opening_hours",
        "nutzungsentgelt": "usage_fee",
        "zahlungsart": "payment_type",
        "barrierefrei": "wheelchair_accessible",
        "barrierearm": "low_barrier",
        "wickeltisch": "changing_table",
    },

    # ---------------------------------------------------------------------
    # baumbestand  (park/facility trees + street trees)
    # Both layers feed one `trees` table. The orchestrator injects
    # `tree_type` from datasets.yaml so the source family stays visible.
    # ---------------------------------------------------------------------
    ("baumbestand", "anlagenbaeume"): {
        "gisid": "gis_id",
        "pitid": "pit_id",
        "standortnr": "tree_number",
        "kennzeich": "object_number",
        "namenr": "object_name",
        "art_dtsch": "species_de",
        "art_bot": "species_botanical",
        "gattung_deutsch": "genus_de",
        "gattung": "genus",
        "art_gruppe": "species_group",
        "pflanzjahr": "planting_year",
        "standalter": "age_years",
        "kronedurch": "crown_diameter_m",
        "stammumfg": "trunk_circumference_cm",
        "baumhoehe": "height_m",
        "eigentuemer": "owner",
        "bezirk": "district",
    },
    ("baumbestand", "strassenbaeume"): {
        "gisid": "gis_id",
        "pitid": "pit_id",
        "standortnr": "tree_number",
        "kennzeich": "object_number",
        "namenr": "object_name",
        "art_dtsch": "species_de",
        "art_bot": "species_botanical",
        "gattung_deutsch": "genus_de",
        "gattung": "genus",
        "art_gruppe": "species_group",
        "strnr": "street_number",
        "strname": "street_name",
        "hausnr": "house_number",
        "zusatz": "house_number_suffix",
        "pflanzjahr": "planting_year",
        "standalter": "age_years",
        "kronedurch": "crown_diameter_m",
        "stammumfg": "trunk_circumference_cm",
        "baumhoehe": "height_m",
        "eigentuemer": "owner",
        "bezirk": "district",
    },

    # ---------------------------------------------------------------------
    # kita  (Kindertagesstätten)
    # ---------------------------------------------------------------------
    ("kita", "kita"): {
        "name": "name",
        "traeger": "operator",
        "strasse": "street",
        "hausnr": "house_number",
        "plz": "postal_code",
        "bezirk": "district",
        "ortsteil": "neighborhood",
        "telefon": "phone",
        "email": "email",
        "internet": "website",
    },

    # ---------------------------------------------------------------------
    # gewaesserkarte  (water bodies — surface polygons)
    # ---------------------------------------------------------------------
    ("gewaesserkarte", "e_gew_gewaesser_fl"): {
        "gewnralt": "water_number_old",
        "typ": "water_type",
        "gewname": "name",
        "gewrneu": "water_number_new",
        "neuer_bezi": "district",
        "ortsteil": "neighborhood",
        "vorfluter": "receiving_water",
        "gewflqm": "surface_area_m2",
        "gewlm": "length_m",
        "eigent": "owner",
        "unterhaltu": "maintenance",
        "gewart": "water_kind",
        "gewordng": "water_class",
        "bemerkunge": "notes",
    },
    # ---------------------------------------------------------------------
    # alkis_gebaeude  (ALKIS building footprints)
    # Keep only named buildings — `nam` is the primary Name field in ALKIS.
    # See context_ingestion/documentation/alkis_gebaeude.txt for source column list.
    # ---------------------------------------------------------------------
    ("alkis_gebaeude", "gebaeude"): {
        "nam": "name",
        "bezeich": "description",
        "namlag": "street_name",
        "hnr": "house_number",
        "pnr": "pseudo_number",
        "shape_area": "area_m2",
        "aog": "num_storeys",
    },
    # ---------------------------------------------------------------------
    # alkis_ortsteile / alkis_bezirke (administrative polygons)
    # ---------------------------------------------------------------------
    ("alkis_ortsteile", "ortsteile"): {
        "nam": "name",
    },
    ("alkis_bezirke", "bezirke"): {
        "nam": "name",
    },
    ("alkis_bezirke", "alkis_bezirke:bezirke"): {
        "nam": "name",
    },
}
