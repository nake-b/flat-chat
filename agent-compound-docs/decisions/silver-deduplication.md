# Silver deduplication — collapsing reposted flats

**Status:** Implemented June 2026. `deduplicate` added to
`services/ingestion/src/silver/transformer.py`, wired into `silver.run`, covered
by `services/ingestion/tests/integration/test_silver_deduplication.py`. A one-off
manual cleanup removed 167 of 1787 existing listings (93 duplicate groups).

**Related docs:**
- [`gold-platinum-layers.md`](gold-platinum-layers.md) — the medallion layout
- [`schema-ownership-split.md`](schema-ownership-split.md) — `world` schema ownership

## Problem

Letting agencies repost the same apartment many times — usually just a price
change — pollutes search with near-identical cards. Each repost carries its own
`external_id`, so the existing UPSERT key `(source_name, external_id)` admits
every one as a distinct `world.listings` row. The rows are otherwise identical:
same `title`, same `address`. We measured 93 such groups (167 redundant rows) in
a 1787-row table.

## Decision

A second pass after `transform` — `deduplicate(session)` — runs one
window-function `DELETE` that keeps a single survivor per `(title, address)`
group and removes the rest. Cascades (`ON DELETE CASCADE` on geo-context,
embeddings, and the six `listings_nearby_*`) clean up the dependent rows.

- **Match key:** `(btrim(title), btrim(address))`, **across any source**. A flat
  cross-posted on wg-gesucht and kleinanzeigen collapses to one row. In the real
  data every duplicate group was within a single source anyway, so cross-source
  matching costs nothing and is the more honest rule.
- **Survivor:** `ORDER BY (location IS NOT NULL) DESC, scraped_at DESC,
  ingested_at DESC, id DESC` — prefer a row that still has coordinates (a
  coordinate-less listing is filtered out of search, see `silver/upsert.py`),
  then newest scrape; the last two keys only break ties for determinism.
- **Guard:** only rows with **both** a non-blank `title` and `address`
  participate. NULL or whitespace-only values must never collapse together.

It runs on **every** `silver.run`, not just once. Bronze `raw_listings` rows
survive a silver delete (`listings.raw_listing_id → raw_listings.id` is
`ON DELETE SET NULL`) and `transform` reprocesses all of bronze with no
incremental filter, so the next run re-inserts anything a one-off cleanup
removed. Placing `deduplicate` before the gold chain means deleted rows are
never enriched.

## Rejected alternatives

- **A partial unique index on `(title, address)` + `ON CONFLICT`.** Postgres
  `ON CONFLICT` can target only one constraint/index per statement, and the
  transformer already targets `uq_listing_source_external`. A second unique
  index would make a duplicate INSERT *raise* (unhandled) and crash the
  transform. A constraint also can't express "prefer the geocoded row."
- **In-loop skip inside `transform`.** More bookkeeping (track seen keys, query
  existing rows), and it wouldn't self-heal duplicates already sitting in silver
  from before the change. The post-pass DELETE is simpler and idempotent.
