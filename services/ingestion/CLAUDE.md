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
    _lib/stealth.js     → Shared browser stealth: puppeteer-extra + stealth
                          plugin, rotating CURRENT Chrome UA pool + matching
                          client hints. See "Scraper anti-bot" below.
  silver/               → Bronze → typed Listing rows
    run.py              → `python -m silver.run` — chains gold + platinum at end
    transformer.py      → Dispatcher by source; UPSERT logic + `deduplicate`
                          (collapses reposted same-title+address flats) +
                          geocoding pass that backfills NULL coordinates via
                          Nominatim (tail of `transform()`)
    sources/            → Per-source transformers (kleinanzeigen, wg_gesucht,
                          housinganywhere, wohninberlin)
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

## Scraper anti-bot / stealth

All Node scrapers share `scraper/_lib/stealth.js` (`require('scraper-lib/stealth')`):

- `makeStealthPuppeteer(require('puppeteer'))` wraps the engine with
  puppeteer-extra + puppeteer-extra-plugin-stealth — hides `navigator.webdriver`,
  gives a real `PluginArray` (the old hand patch set it to `[1,2,3,4,5]`, an
  integer tell), patches the `Runtime.enable` CDP leak, etc.
- `applyStealthToPage(page, {userAgent, profile, acceptLanguage, timeoutMs})`
  replaces every old per-scraper `preparePage`. It rotates a CURRENT
  desktop-Chrome UA **per run** (not per request — real browsers don't change UA
  mid-session) with a MATCHING `userAgentMetadata`, so the UA string and the
  `sec-ch-ua` client hints agree, and aligns `navigator.languages` to
  `Accept-Language`. Returns the chosen `{userAgent, metadata}` so a multi-tab
  scraper reuses one UA across tabs via `profile:`.
- `detectChallenge(page)` covers Cloudflare + DataDome + visible captcha iframes.

Search-page `goto` uses `waitUntil: 'domcontentloaded'` (not `networkidle2`) so a
challenge interstitial fails fast and `detectChallenge` can report it, instead of
silently eating the full timeout.

**Maintenance: bump `CHROME_BUILDS` in `_lib/stealth.js` every few Chrome
releases.** A stale UA pool is the exact failure this module exists to prevent —
the scrapers shipped pinned to Chrome 124 (April 2024), and by mid-2026 that
version alone was a bot signal. Deps live in `_lib/package.json` only; each
scraper keeps its own `puppeteer` and passes it in. `USER_AGENT=` pins a UA;
`HEADLESS=false` runs headful for debugging.

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

- `silver.run` → `transform` → `deduplicate` → `gold.run.main([])` → then
  attempts `platinum.run.main([])` best-effort (skipped silently if
  `JINA_API_KEY` isn't set — semantic search degrades to recency, no fatal
  failure). **`transform` itself** ends with a best-effort geocoding pass
  (`_geocode_missing`) that fills NULL coordinates by geocoding the listing's
  address (Nominatim by default) — so coordinate-less sources (e.g.
  wohninberlin) become visible to search + gold. Only `location IS NULL` rows
  are touched (idempotent), and `upsert.py` preserves the backfilled point
  across re-transforms; a geocoder outage warns but never fails silver.
  Configure via `NOMINATIM_BASE_URL` / `GEOCODER_USER_AGENT` /
  `GEOCODER_RATE_LIMIT_S`. `deduplicate` (in `transformer.py`) runs a single
  window-function
  DELETE that collapses listings sharing a `(title, address)` — any source —
  down to one survivor (geocoded-first, then newest `scraped_at`). It runs
  **before** gold so deleted rows are never enriched, and runs on **every**
  silver run rather than once: bronze `raw_listings` rows survive a silver
  delete (FK is `ON DELETE SET NULL`) and `transform` reprocesses all of bronze,
  so a one-off cleanup would be re-undone next run. See
  [`silver-deduplication.md`](../../agent-compound-docs/decisions/silver-deduplication.md).
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
