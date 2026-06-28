# geo_context — Berlin geospatial context ETL

Ships Berlin geo-reference data (schools, kitas, parks, playgrounds,
strategic noise, population density, hospitals, water bodies, landmarks,
bezirke/ortsteile admin polygons, the inner-city / Umweltzone ring, public
transit) into PostGIS so the chat agent can answer "what kind of
neighborhood is this?" questions about a listing. (MSS/Sozialmonitoring was
removed in geo-context v2.)

```
┌──────────────────────┐    XML/JSON   ┌────────────────────┐
│ Berlin GDI WFS       │──────────────▶│ extract/wfs.py     │
│ gdi.berlin.de/wfs/…  │               │ (GeoDataFrame)     │
└──────────────────────┘               └─────────┬──────────┘
                                                 │
┌──────────────────────┐    zip CSVs   ┌─────────▼──────────┐
│ VBB GTFS feed        │──────────────▶│ extract/gtfs.py    │
│ vbb.de/vbbgtfs       │               │ (DataFrames)       │
└──────────────────────┘               └─────────┬──────────┘
                                                 │
                                       ┌─────────▼──────────┐
                                       │ transform/         │
                                       │  - rename (DE→EN)  │
                                       │  - project to 4326 │
                                       │  - station collapse│
                                       └─────────┬──────────┘
                                                 │
                                       ┌─────────▼──────────┐
                                       │ load/postgis.py    │
                                       │ TRUNCATE + INSERT  │
                                       │ in one transaction │
                                       └─────────┬──────────┘
                                                 │
                              ┌──────────────────▼──────────────────┐
                              │ silver-tier PostGIS tables          │
                              │  schools, school_catchments, kitas, │
                              │  population_density_2025,           │
                              │  strategic_noise_2022, green_volume,│
                              │  parks, playgrounds, hospitals,     │
                              │  disabled_parking, landmarks,       │
                              │  bezirke, ortsteile,                │
                              │  inner_city_zone, water_bodies,     │
                              │  transit_stops, transit_routes,     │
                              │  transit_route_shapes               │
                              └─────────────────────────────────────┘
```

Cadence ≠ listings. WFS context refreshes **yearly to monthly** (mostly
yearly); GTFS **weekly**. Listings refresh **daily**. So `geo_context`
runs as a **separate compose profile**, never alongside `silver.run`.

## Directory layout

```
services/ingestion/src/geo_context/
├── README.md            ← you are here
├── config.py            ← typed loader for datasets.yaml
├── datasets.yaml        ← source-of-truth: which datasets/layers + status
├── run.py               ← CLI: python -m geo_context.run
├── extract/
│   ├── wfs.py           ← BerlinGdiWfsClient (GetCapabilities + GetFeature)
│   └── gtfs.py          ← VbbGtfsClient (download zip → 5 needed tables)
├── transform/
│   ├── aliases.py       ← German→English column maps per (dataset, layer)
│   ├── wfs.py           ← reproject + rename + filter to silver columns
│   └── gtfs.py          ← station collapse, modes/lines, canonical shapes
├── load/
│   └── postgis.py       ← transactional truncate+insert
├── tests/               ← pytest, no network
└── icebox/              ← parked code, see icebox/README.md
```

## Running

### Dev

```bash
# 1. Make sure migrations are applied (creates the silver tables)
docker compose run --rm backend uv run alembic upgrade head

# 2. Run the full pipeline (downloads ~50MB of GTFS + WFS GeoJSON)
docker compose --profile geo-context run --rm geo-context

# Restrict to a few datasets:
docker compose --profile geo-context run --rm geo-context \
    python -m geo_context.run --only schools,parks

# Skip GTFS (handy when iterating on WFS aliases):
docker compose --profile geo-context run --rm geo-context \
    python -m geo_context.run --skip-gtfs
```

### Production / scheduled

There's **no compose-level scheduling** — this service is opt-in via the
`geo-context` profile and won't run on a plain `docker compose up`. Wire
scheduling at the deployment layer (host cron, cloud scheduler, etc.).
Suggested cadence:

- **Weekly:** GTFS only
  `docker compose --profile geo-context run --rm geo-context python -m geo_context.run --only gtfs`
- **Quarterly:** full run

## Data sources

| Table | Source URL | License | Update cadence |
|---|---|---|---|
| `schools` + `school_catchments` | gdi.berlin.de/services/wfs/schulen | dl-de/by-2-0 | yearly |
| `kitas` | gdi.berlin.de/services/wfs/kitas | dl-de/zero-2-0 | monthly |
| `population_density_2025` | gdi.berlin.de/services/wfs/ua_einwohnerdichte_2025 | dl-de/by-2-0 | yearly |
| `strategic_noise_2022` | gdi.berlin.de/services/wfs/ua_stratlaerm_2022 | dl-de/by-2-0 | every 5y (EU) |
| `green_volume_2020` | gdi.berlin.de/services/wfs/ua_gruenvolumen_2020 | dl-de/by-2-0 | every ~5y |
| `parks` + `playgrounds` | gdi.berlin.de/services/wfs/gruenanlagen | dl-de/by-2-0 | quarterly |
| `hospitals` (plan + other) | gdi.berlin.de/services/wfs/krankenhaeuser | dl-de/by-2-0 | rarely |
| `disabled_parking` | gdi.berlin.de/services/wfs/behindertenparkplaetze | dl-de/by-2-0 | monthly |
| `landmarks` (ALKIS) | gdi.berlin.de/services/wfs/alkis_gebaeude | dl-de/zero-2-0 | yearly |
| `landmarks` (OSM) | overpass-api.de (Overpass) | ODbL | — |
| `bezirke` | gdi.berlin.de/services/wfs/alkis_bezirke | dl-de/zero-2-0 | rarely |
| `ortsteile` | gdi.berlin.de/services/wfs/alkis_ortsteile | dl-de/zero-2-0 | rarely |
| `inner_city_zone` (Umweltzone) | gdi.berlin.de/services/wfs/umweltzone | dl-de/zero-2-0 | rarely |
| `water_bodies` | gdi.berlin.de/services/wfs/gewaesserkarte | dl-de/by-2-0 | rarely |
| `transit_stops` + `transit_routes` + `transit_route_shapes` | vbb.de/vbbgtfs | CC BY 4.0 | weekly |

Berlin GDI data is published under Datenlizenz Deutschland — `by-2-0`
(attribution required) or `zero-2-0` (no attribution). OSM is ODbL — keep
"© OpenStreetMap contributors" in any UI that surfaces landmark data.

## Adding a new dataset

1. **Discover the layer name** (WFS only):
   ```bash
   curl 'https://gdi.berlin.de/services/wfs/<dataset>?service=WFS&version=2.0.0&request=GetCapabilities' \
     | xmllint --xpath '//*[local-name()="Name"]/text()' -
   ```
   Or use `BerlinGdiWfsClient.get_capabilities()` from a Python shell.

2. **Inspect the schema** to write the alias map:
   ```bash
   curl 'https://gdi.berlin.de/services/wfs/<dataset>?service=WFS&version=2.0.0&request=DescribeFeatureType&typeNames=<layer>'
   ```

3. **Write the migration** — add a `CREATE TABLE` + GIST index block to a
   fresh alembic revision (see `services/backend/alembic/versions/0003_*`
   for the pattern). English names only.

4. **Add the alias map** in `transform/aliases.py` keyed by `(dataset, layer)`.

5. **Add a YAML entry** in `datasets.yaml` with `enabled: true, status: wip`.

6. **Run** `python -m geo_context.run --only <key>` and sanity-check.

## Reviving an iceboxed dataset

See `icebox/README.md` — short version: copy the migration block into a
fresh alembic revision, merge the alias map into `transform/aliases.py`,
add a YAML entry.

## Quirks worth knowing

- **CRS:** Berlin GDI publishes in **EPSG:25833** (ETRS89 / UTM zone 33N,
  meters). We project to **EPSG:4326** in the Transform step so silver
  is consistent with the `listings.location` column. PostGIS spatial
  joins should always go through `::geography` for meter-accurate results.

- **`wip`-status layers (kitas, landmarks, bezirke, ortsteile, umweltzone,
  Gewässer):** alias column names are based on GetCapabilities +
  DescribeFeatureType but not all verified against real output. If an alias
  is wrong the column is silently dropped — check `SELECT COUNT(*)` +
  `SELECT name FROM <table> LIMIT 5` after the first run. In particular,
  `bezirke` aliases `{"namgem": "name", "name": "bezirk_id"}` because the
  layer's `name` is a numeric id and `namgem` is the human label.

- **OSM landmarks (`extract/osm.py`):** a separate Overpass step (not WFS)
  appends `source='osm'` rows into `landmarks` after the ALKIS seed. Overpass
  is flaky → retry/backoff; a Geofabrik-extract fallback is a TODO.

- **VBB uses GTFS Extended Route Types**, not the basic 0–3 codes. The
  values you'll see in `transit_stops.modes_served` / `transit_routes.route_type`:

  | Code | Mode | Berlin example |
  |---|---|---|
  | `3` | Bus (basic-spec subset) | a few legacy bus lines |
  | `100` | Mainline rail | ICE, FEX, IC |
  | `106` | Regional rail | RB12, RB14, RE1, RE7 |
  | `109` | Suburban rail (S-Bahn) | S1, S3, S5, S7, S8, S9 |
  | `400` | Urban metro (U-Bahn) | U1, U2, U3, U5, U6, U7, U8, U9 |
  | `700` | Bus | all city buses + N-buses |
  | `900` | Tram | M1, M4, M5, M6, M10, 12, 16, … |
  | `1000` | Water transport (ferry) | F10, F11, F12, F23, … |

  Querying "near a U-Bahn" is `400 = ANY(modes_served)`. Near any rail is
  `modes_served && ARRAY[100, 106, 109, 400]`. See the
  [GTFS Extended Route Types spec](https://developers.google.com/transit/gtfs/reference/extended-route-types).

- **GTFS station collapse is imperfect on the VBB feed.** When VBB
  populates `parent_station`, we fold platform children onto the parent
  (Alexanderplatz's U-Bahn platforms collapse cleanly). But VBB
  *also* publishes the same logical station name as multiple separate
  stops with no `parent_station` link — e.g. "S+U Alexanderplatz
  Bhf/Memhardstr. (Berlin)" appears 3× because each bus-stop pole on
  the same street is its own GTFS row. Spatial queries from the agent
  still return correct distances, just possibly to multiple "Alex"
  rows. Further deduplication would need name-based clustering, which
  is fuzzy — deferred.

- **`location_type` 2/3/4 are filtered out.** GTFS spec allows
  `location_type` to be `2` (entrance/exit), `3` (generic node), or
  `4` (boarding area). VBB uses 2 and 3 heavily — those would
  otherwise win the location-type-DESC drop_duplicates and force the
  canonical row onto a non-boardable point. We only keep `0` and `1`.

- **No wheelchair-accessibility info.** GTFS spec has a
  `wheelchair_boarding` field, but the VBB feed populates it for only
  ~3% of stops (all with value 1=accessible). That's no usable signal,
  so we dropped the column in `0004_geo_context_hardening`. If VBB
  improves coverage, re-add per `extract/gtfs.py` and add it back to
  the migration.

- **Self-intersecting source polygons** in
  `green_volume_2020` (id 629) and `water_bodies.Müggelspree`.
  `transform/wfs.py` calls `shapely.make_valid` on every layer, so
  re-runs land clean. Existing rows are repaired by migration `0004`.

- **GTFS routes need a hash prefix:** the GTFS spec stores colors as
  `FFD800`, the frontend expects `#FFD800`. We prepend `#` in `build_routes`.

- **Hospitals tier discriminator:** the unified `hospitals` table is fed
  from two WFS layers; the orchestrator passes `extra_columns={"tier": …}`
  through to the transformer so each row knows its source. Plan layer
  loads first (truncate + append); the "other" layer appends.

- **Empty layers don't fail the run:** orchestrator catches per-layer
  exceptions and logs them, so one broken layer doesn't take down the
  other 13.

- **No personal data:** every source we ingest is public-record open
  data. Schools include public contact info (phone, email, website) —
  that's intended and published by Berlin.
