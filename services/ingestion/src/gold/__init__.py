"""Gold layer — pre-joined per-listing geo-context.

The gold tier (`listings_geo_context`) denormalises everything the chat
agent's search + detail surfaces need into a single row per listing. The
search hot-path then reads gold via plain B-tree filters; the detail
panel reads tier-3 JSONB blobs via a single PK lookup. The spatial work
that used to happen at query time (LATERAL KNN, ST_DWithin, ST_Contains,
ST_Area∘ST_Intersection) is paid here once per listing and amortised
across every request.

Run cadence:
  - Chained after `silver.run` (daily, when new listings arrive)
  - Chained after `geo_context.run` (monthly, when geo-context refreshes
    invalidate all listings' enrichment)
  - Standalone via `python -m gold.run` (or `docker compose --profile gold
    run --rm gold`) when re-enrichment is needed without scraping/silver
    work.

Architecture-decision doc: `agent-compound-docs/decisions/gold-platinum-layers.md`
"""
