# population_density_change_entw — iceboxed

**Source layer:** `ua_einwohnerdichte_2025` / `ua_einwohnerdichte_2025_entw`

**Why iceboxed:** PR #8 review (2026-06-13). The `_entw` (Entwicklung)
variant is the year-over-year change of population density. Useful
in principle for "is this area gentrifying / depopulating?" questions,
but the signal-to-noise ratio on a single 1-year delta is too low to
inform an apartment search today. The non-`_entw` table
(`population_density_2025`) gives the agent the absolute level it needs.

**To revive:**
1. Drop `migration_block.py` into a fresh alembic revision body.
2. Merge the alias map from `transform.py` into `geo_context/transform/aliases.py`.
3. Add to `datasets.yaml`:
   ```yaml
     - key: population_density_change_2024_2025
       dataset: ua_einwohnerdichte_2025
       layer: ua_einwohnerdichte_2025_entw
       table: population_density_change_2024_2025
       geom_type: MultiPolygon
       enabled: true
       status: wip
       license: "dl-de/by-2-0"
       source_url: "https://gdi.berlin.de/services/wfs/ua_einwohnerdichte_2025"
       update_cadence: yearly
       description: "Year-over-year population density change 2024→2025."
   ```
4. Run `python -m geo_context.run --only population_density_change_2024_2025`.
