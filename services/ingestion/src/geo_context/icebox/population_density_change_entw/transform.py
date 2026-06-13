"""Ready-to-paste alias map for the deferred _entw layer.

NOT IMPORTED. To revive, merge this dict into geo_context/transform/aliases.py.
"""

_ALIASES = {
    ("ua_einwohnerdichte_2025", "ua_einwohnerdichte_2025_entw"): {
        "schluessel": "lor_key",
        "ew2024": "population_2024",
        "ew2025": "population_2025",
        "flalle": "area_total",
        "ha": "area_hectares",
        "ew_ha_2024": "population_per_hectare_2024",
        "ew_ha_2025": "population_per_hectare_2025",
        "diff_2025_2024": "diff_2025_2024",
        "typklar": "area_type",
        "etypklar": "area_type_en",
    },
}
