# flat-chat ingestion

Three-tier data pipeline that scrapes Berlin apartment listings, dumps them raw into Postgres, and normalizes them into a single queryable table.

> **Geo-context** (parks, schools, noise, transit, …) is a **separate** pipeline that runs on its own cadence — see [src/geo_context/README.md](src/geo_context/README.md).

```
┌──────────────────┐    pg     ┌──────────────┐
│ cardscraper.js   │──────────▶│ iron_cards   │   raw card data from search/list pages
└──────────────────┘           └──────┬───────┘
                                      │ WHERE detail_scraped_at IS NULL
                                      ▼
┌──────────────────┐    pg     ┌──────────────┐
│ detailscraper.js │──────────▶│ raw_listings │   raw detail-page JSON (bronze)
└──────────────────┘           └──────┬───────┘
                                      │
                                      ▼
                              ┌──────────────┐
                              │ silver.run   │   normalized, typed columns
                              └──────┬───────┘
                                     ▼
                              ┌──────────────┐
                              │ listings     │   the main app-facing table
                              └──────────────┘
```

Each tier is independently runnable and idempotent. The detail scraper uses an `iron_cards.detail_scraped_at` cursor to resume mid-run — kill it, restart, it picks up where it stopped.

## Directory layout

```
services/ingestion/src/
├── iron/loader.py         # CLI: JSON-replay → iron_cards
├── bronze/loader.py       # CLI: JSON-replay → raw_listings (also flips iron cursor)
├── silver/
│   ├── run.py             # CLI: bronze → listings
│   ├── transformer.py     # source dispatcher
│   └── sources/
│       ├── common.py      # shared parsing helpers
│       ├── wg_gesucht.py  # WG-gesucht-specific row mapping
│       └── kleinanzeigen.py
├── scraper/
│   ├── _lib/              # shared Node DB helper (pg + dotenv)
│   ├── kleinanzeigen/
│   └── wg-gesucht/
├── db.py, config.py
```

## First-time setup

```bash
# 1. Apply migrations (creates iron_cards, raw_listings, listings, pgvector extension)
docker compose run --rm backend uv run alembic upgrade head

# 2. Install Node deps once (each scraper subdir + the shared _lib)
cd services/ingestion/src/scraper/_lib            && npm install
cd ../kleinanzeigen                                && npm install
cd ../wg-gesucht                                   && npm install
```

After that, no inline env vars are needed — `.env` at the repo root is auto-loaded by both Python (`python-dotenv` in `config.py`) and Node (`dotenv` in `_lib/db.js`).

## Live scraping

**Make sure your VPN is on** — both endpoints rate-limit aggressively.

```bash
# wg-gesucht (~8s/listing, fast)
cd services/ingestion/src/scraper/wg-gesucht && npm run scrape:cards     # search → iron
cd services/ingestion/src/scraper/wg-gesucht && npm run scrape:details   # iron → bronze

# kleinanzeigen (20–30s/listing + 5-min batch pauses, slow)
cd services/ingestion/src/scraper/kleinanzeigen && npm run scrape:cards
cd services/ingestion/src/scraper/kleinanzeigen && npm run scrape:details
```

After detail scrapes finish, normalize bronze → silver:

```bash
cd services/ingestion && PYTHONPATH=src python3 -m silver.run
```

Silver is idempotent — safe to re-run at any time, including while a scraper is still going if you want to peek at progress.

After silver lands fresh rows, populate the `embedding` column so semantic search works:

```bash
cd services/ingestion && PYTHONPATH=src python3 -m silver.embed
```

`silver.embed` posts text in batches to Jina v3 (`retrieval.passage` task) and updates rows where `embedding IS NULL`. Idempotent — re-running is a no-op for already-embedded rows. Requires `JINA_API_KEY` in `.env`. Skip the step if you don't have a Jina key — structured search still works without embeddings.

## JSON replay (no scraping)

The committed `*.json` / `*-detail.json` files act as fixtures — useful for working without re-scraping.

```bash
cd services/ingestion
PYTHONPATH=src python3 -m iron.loader  src/scraper/kleinanzeigen/kleinanzeigen.json
PYTHONPATH=src python3 -m iron.loader  src/scraper/wg-gesucht/wggesucht.json
PYTHONPATH=src python3 -m bronze.loader src/scraper/kleinanzeigen/kleinanzeigen-detail.json
PYTHONPATH=src python3 -m bronze.loader src/scraper/wg-gesucht/wggesucht-detail.json
PYTHONPATH=src python3 -m silver.run
```

The bronze loader also flips `iron_cards.detail_scraped_at` for any iron card it replays into, so the next live detail-scrape doesn't redundantly re-scrape those listings.

## Cursor behavior

| Action | Effect on `iron_cards.detail_scraped_at` |
|---|---|
| `npm run scrape:cards` | Doesn't touch — only inserts/updates card data |
| `npm run scrape:details` | Sets to `now()` after each successful bronze write |
| `python -m iron.loader …` | Doesn't touch — replaying card JSON never undoes detail progress |
| `python -m bronze.loader …` | Sets to the bronze record's `scraped_at` |

To force re-scrape of a specific listing's detail:

```sql
UPDATE iron_cards SET detail_scraped_at = NULL
WHERE source_name = 'kleinanzeigen' AND external_id = '3383281098';
```

## Adding a new source

1. Build a Node card scraper that writes to `iron_cards` (model on `wg-gesucht/wggesuchtscraper.js`).
2. Build a Node detail scraper that reads pending iron rows and writes to `raw_listings`.
3. Add `services/ingestion/src/silver/sources/<source>.py` with a `to_listing_row(raw) -> dict` function.
4. Register it in `silver/transformer.py`'s dispatcher.

No schema migration needed — the `listings` table is intentionally the union of every column any source might produce.

## Quirks worth knowing

- **`scraper-lib` is copied** into each scraper's `node_modules` (npm `file:` deps don't symlink). If you edit `_lib/db.js`, refresh the copies: `rm -rf node_modules/scraper-lib && npm install` in each scraper subdir.
- **Kleinanzeigen listings don't expose street addresses** — only `postcode + district` + lat/lng. The silver transformer reflects that. Reverse-geocoding is a future enrichment step.
- **WG-gesucht amenities are comma-separated** strings like `"Bedarfsausweis, Baujahr 2019, Energieeffizienzklasse B"`. The transformer regex-extracts energy fields from them; everything else stays raw in the `features` JSONB.
- **No personal data** is ever stored — listers' names, phones, and member-since strings are dropped at the silver transform.
