# services/ingestion/CLAUDE.md

Ingestion-specific context for Claude Code. The root CLAUDE.md has
project-wide tech stack + conventions; this file documents the ETL
pipelines.

## Layout

```
alembic/                → OWNS the `world` schema migrations (ported from the
                          backend; revision IDs preserved). world.alembic_version.
src/
  config.py             → Catalog, WfsDataset loading from datasets.yaml
  db.py                 → engine (search_path=world,public), SessionLocal, get_session()
  iron/, bronze/        → (iron + bronze tiers; raw scraper output)
  scraper/              → Per-source Node scrapers (NEVER run automatically)
  silver/               → Bronze → typed Listing rows
    run.py              → `python -m silver.run` — chains gold + platinum at end
    transformer.py      → Dispatcher by source; UPSERT logic
    sources/            → Per-source transformers (kleinanzeigen, wg_gesucht)
  geo_context/          → Berlin GDI WFS + VBB GTFS → silver geo tables
    run.py              → CLI with --only / --skip-wfs / --skip-gtfs
    extract/, transform/, load/
    datasets.yaml       → Source-of-truth catalog
  gold/                 → NEW. Joined per-listing geo-context.
    enrich_listings.py  → One bulk-SQL UPSERT per chip family
    run.py              → CLI with --only chip1,chip2 flag
  platinum/             → NEW. Vector embeddings (Jina v3, 1024 dims).
    embed.py            → Calls Jina API, UPSERTs into listings_embeddings
    run.py              → CLI with --reembed / --since flags
```

## Migrations — this service owns the `world` schema

All tables below live in the **`world`** Postgres schema, and this service's
Alembic (`alembic/`, tracked in `world.alembic_version`) is the authoritative
DDL. The backend keeps read-only ORM over them + a drift test.

```bash
# Requires the postgres bootstrap to have created the world/app schemas +
# extensions first (services/postgres/init/ on a fresh volume, or
# ./scripts/bootstrap-schemas.sh on an existing one).
docker compose run --rm ingestion uv run alembic upgrade head
```

`db.py`'s engine pins `search_path = world, public`, so all raw ETL SQL
(`to_sql`/`to_postgis`, enrich UPSERTs) resolves unqualified names to `world`.
Full record: [`schema-ownership-split.md`](../../agent-compound-docs/decisions/schema-ownership-split.md).

## Medallion tiers in this service

| Tier | Tables | Cadence | Module |
|---|---|---|---|
| **Iron** | `iron_cards` | Daily | `iron/` (filled by `scraper/`) |
| **Bronze** | `raw_listings` | Daily | `bronze/` (filled by `scraper/`) |
| **Silver — listings** | `listings` | Daily | `silver/` (reads bronze) |
| **Silver — geo-context** | `transit_stops`, `parks`, `schools`, `kitas`, `landmarks`, `bezirke`, `ortsteile`, `inner_city_zone`, ... | Monthly | `geo_context/` |
| **Gold** | `listings_geo_context` | Chained after silver listings + chained after geo-context | `gold/` |
| **Platinum** | `listings_embeddings` | Chained after silver listings (best-effort) | `platinum/` |

Decision doc: [`gold-platinum-layers.md`](../../agent-compound-docs/decisions/gold-platinum-layers.md).

## Chain triggers

- `silver.run` → calls `gold.run.main([])` at the end. Then attempts
  `platinum.run.main([])` best-effort (skipped silently if `JINA_API_KEY`
  isn't set — semantic search degrades to recency, no fatal failure).
- `geo_context.run` → if any WFS/GTFS family succeeded, calls
  `gold.run.main([])`. Geo-context refreshes invalidate every listing's
  gold row, so the chain ensures fresh enrichment without manual
  intervention.

## NEVER run scraper unless explicitly asked

The project convention is that scraper runs are **manual + intentional**.
Local development uses existing bronze data — `silver.run` → `gold.run`
→ `platinum.run` is a closed loop that doesn't need new listings.

`docker compose --profile ingestion run --rm ingestion` runs silver only
(which chains gold + platinum); scraper is in a separate compose
profile that must be invoked explicitly.

## Landmarks: ALKIS (WFS) + OSM (Overpass)

`landmarks` is the one named-place class with no pre-existing table. It is
seeded from ALKIS named building footprints (a WFS layer, `source='alkis'`,
`category='building'`, named-only filter in `transform/wfs.py`) and then
APPENDED to from OSM via a separate Overpass step (`extract/osm.py`,
`source='osm'`, `category` from the matched tag). Geometry is mixed
(`geometry(Geometry, 4326)`) and native — OSM nodes stay points, bridges
stay lines, areas stay polygons. OSM is ODbL; ALKIS/GDI is `dl-de/zero-2-0`.

Decision doc: [`geo-context-pipeline.md`](../../agent-compound-docs/decisions/geo-context-pipeline.md).

## Gold chip families

Each function in `gold/enrich_listings.py` is a single bulk-SQL UPSERT
keyed by chip family. The registry `CHIP_FAMILIES` powers the `--only`
flag:

```bash
docker compose --profile gold run --rm gold                       # all
docker compose --profile gold run --rm gold --only nearby_transit,noise  # subset
```

Family names (in `CHIP_FAMILIES` run order):

**Junction-table fillers** — populate `listings_nearby_*` with top-K=5 ∪
all-within-R per listing:
- `nearby_transit` (R=5 km), `nearby_schools` (R=5 km),
  `nearby_kitas` (R=3 km), `nearby_hospitals` (R=12 km),
  `nearby_parks` (R=5 km, cemeteries excluded),
  `nearby_playgrounds` (R=3 km), `nearby_water` (R=6 km),
  `nearby_landmarks` (R=2 km, notable categories only).

**Derived chip scalars** — read from the junction tables:
- `chip_scalars` (nearest_transit_* + nearest_park_* on
  `listings_geo_context`).

**Scalar / field fillers** — properties of the listing's location:
- `noise` (50 m coverage gate; out → NULL; search optimistic-includes;
  writes Lden + Lnight), `greenery` (300 m composite m²),
  `density` (LOR ppl/ha), `admin_areas` (smallest-containing Bezirk +
  Ortsteil via `ST_Covers`), `inside_ring` (inside the Umweltzone polygon),
  `school_catchment` (polygon membership),
  `disabled_parking` (count within 300 m).

Threshold constants (radii, gate distances) are duplicated inline at
the top of `enrich_listings.py` — same values as
`services/backend/src/flat_chat/listings/thresholds.py`. Kept inline
because the ingestion service intentionally does NOT import from the
backend.

See [`spatial-neighbor-tables.md`](../../agent-compound-docs/decisions/spatial-neighbor-tables.md)
for the junction-table rationale + the 50 m noise-gate sources.

## Platinum embedding

`platinum/embed.py` calls Jina v3 (`https://api.jina.ai/v1/embeddings`)
in batches of 64. Free-tier `JINA_API_KEY` is enough for small backfills.
Idempotent — re-running upserts the same embedding under the same
`model_name`; `--reembed` regenerates everything (useful for swapping
models).

Documents are embedded with the **`retrieval.passage`** LoRA (`JINA_TASK`).
Jina v3 is asymmetric: passages must pair with `retrieval.query`-embedded
search queries (the backend's `JinaTaskEmbedder` sets `retrieval.query` at
search time). Using the wrong/default task degrades ranking. If the task ever
changes, existing rows must be regenerated with `--reembed` — `model_name`
alone doesn't capture the task, so the NOT-EXISTS dedup won't notice.

The Jina call retries transient failures (429 / 5xx / transport errors) via
`tenacity` with exponential backoff, honoring `Retry-After`; a non-retryable
4xx (e.g. 401 bad key) surfaces immediately. `embed_pending` **commits per
batch** (the UPSERTs are idempotent), so a mid-run failure keeps completed
batches and the next run resumes — hence `platinum/run.py` uses a commit-as-
you-go `engine.connect()`, not a begin-once `engine.begin()`. Response items
are re-sorted by Jina's per-item `index` before assignment so a reordered
response can't misassign vectors. Covered by `tests/test_embed.py` (mocked
transport — no network/key needed).

To swap models: change `MODEL_NAME` in `platinum/embed.py`, run
`platinum.run --reembed`. Migration 0005 already declared
`listings_embeddings.model_name` so multiple models can coexist if we
add per-search routing later.

## Running

```bash
# Standard daily flow
docker compose --profile ingestion run --rm ingestion             # silver → gold → platinum

# Monthly geo-context refresh
docker compose --profile geo-context run --rm geo-context         # WFS + GTFS → silver → gold

# Standalone re-enrichment (no scraping, no geo refresh)
docker compose --profile gold run --rm gold                       # gold only
docker compose --profile gold run --rm gold --only transit        # one chip family
docker compose --profile platinum run --rm platinum               # re-embed missing
docker compose --profile platinum run --rm platinum --reembed     # re-embed all
```
