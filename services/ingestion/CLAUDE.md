# services/ingestion/CLAUDE.md

Ingestion-specific context for Claude Code. The root CLAUDE.md has
project-wide tech stack + conventions; this file documents the ETL
pipelines.

## Layout

```
src/
  config.py             → Catalog, WfsDataset loading from datasets.yaml
  db.py                 → engine, SessionLocal, get_session()
  iron/, bronze/        → (iron + bronze tiers; raw scraper output)
  scraper/              → Per-source Node scrapers (NEVER run automatically)
    _lib/stealth.js     → Shared browser stealth: puppeteer-extra + stealth
                          plugin, rotating CURRENT Chrome UA pool + matching
                          client hints. See "Scraper anti-bot" below.
  silver/               → Bronze → typed Listing rows
    run.py              → `python -m silver.run` — chains gold + platinum at end
    transformer.py      → Dispatcher by source; UPSERT logic
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
| **Silver — geo-context** | `transit_stops`, `parks`, `schools`, `social_monitoring_2025`, ... | Monthly | `geo_context/` |
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

## Silver MSS English translation

`geo_context/transform/wfs.py` translates German labels in the MSS
(Sozialmonitoring) dataset to English at silver-transform time. The
single source of truth for the German→English mapping is the
`_VALUE_TRANSLATIONS` table at the top of that file.

Why silver, not gold: silver is the canonical clean form. If the
publisher renames a label, this file changes; nothing downstream sees
German.

Decision doc: [`geo-context-pipeline.md`](../../agent-compound-docs/decisions/geo-context-pipeline.md).

## Gold chip families

Each function in `gold/enrich_listings.py` is a single bulk-SQL UPSERT
keyed by chip family. The registry `CHIP_FAMILIES` powers the `--only`
flag:

```bash
docker compose --profile gold run --rm gold                       # all
docker compose --profile gold run --rm gold --only transit,parks  # subset
```

Family names: `transit`, `parks`, `playground`, `schools`, `hospitals`,
`water`, `noise`, `greenery`, `density`, `mss`, `disabled_parking`.

Threshold constants (cap distances per family) are duplicated inline at
the top of `enrich_listings.py` — same values as
`services/backend/src/flat_chat/listings/thresholds.py`. Kept inline
because the ingestion service intentionally does NOT import from the
backend.

## Platinum embedding

`platinum/embed.py` calls Jina v3 (`https://api.jina.ai/v1/embeddings`)
in batches of 64. Free-tier `JINA_API_KEY` is enough for small backfills.
Idempotent — re-running upserts the same embedding under the same
`model_name`; `--reembed` regenerates everything (useful for swapping
models).

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
