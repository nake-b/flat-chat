# Gold + Platinum layers in the medallion architecture

Decided 2026-06-15 during the search-perf refactor.

## Context

The agent's `search_apartments` was running 5 always-on LATERAL spatial
joins + up to 10 EXISTS subqueries per request; `open_listing` fired 12
sequential sync queries per detail open. Latency grew with the number of
geo-context constraints. The architecture funneled every listing read
through the agent's SSE stream, which doesn't scale to hundreds of
listings + images + bookmarks.

## Decision

Introduce two new medallion layers above silver:

- **Gold (`listings_geo_context`)** — one row per listing, denormalised
  pre-join of every nearby geo-context fact. Computed once at ETL time;
  read at search time via plain B-tree filters.
- **Platinum (`listings_embeddings`)** — semantic-search vectors split
  out from `listings.embedding` so the HNSW index lives only on its
  consumer table, and embedding-model swaps are a platinum-only refresh.

Both layers are populated by their own ingestion modules
(`services/ingestion/src/gold/`, `services/ingestion/src/platinum/`)
with `--only` flags for partial reruns. Chained automatically after
`silver.run` and `geo_context.run`; standalone-runnable when you need
to re-enrich without scraping.

## Rejected alternatives

- **Add new columns to `listings` directly** instead of a separate gold
  table. Pros: no join on read. Cons: blurs silver (clean per-entity)
  with gold (denormalised pre-joined). Picked the separate table — the
  PK FK join is nearly free with the index, and "drop and rebuild gold"
  is a clean operational story.
- **JSONB column on listings** for the geo-context. Cons: harder to
  filter / sort on individual fields without per-row JSONB ops; no per-
  attribute indexes; couldn't isolate gold ETL.
- **Keep computing geo-context at query time** with better caching
  (Redis or pg_stat_statements warming). Doesn't change the algorithmic
  bottleneck — 5 LATERAL × 100 LIMIT × concurrent users still pays the
  spatial cost, just maybe less often.
- **DuckDB embedded** for query-time refinement against the active
  search snapshot. Powerful for analytics but overkill for our scale;
  Postgres + gold table is plenty fast.

## What goes where

| Concern | Layer | Why |
|---|---|---|
| Raw apartment data (price, rooms, location) | silver (`listings`) | Source-faithful per-entity |
| Berlin geo-context raw (transit, parks, MSS polygons, ...) | silver | Source-faithful per-entity |
| MSS German→English label translation | silver (transform step) | Canonical clean form is English; the data layer is language-agnostic |
| Per-listing chip scalars (`nearest_transit_m`, `noise_total_lden`, ...) | gold | Denormalised joins of silver tables |
| Per-listing detail JSONB (top-K schools/parks, greenery profile, ...) | gold | Same |
| Vector embeddings | platinum | Separate from listings; the HNSW index stays with its consumer |
| Bucket labels (`quiet`/`noisy`, `sparse`/`dense`) | NOT in gold | Interpretation, not data — applied at chat-presentation time from `listings.labels` |

The "bucket labels NOT in gold" line is the subtle one: storing raw
numbers in gold and applying threshold labels at result-mapping time
means a threshold tweak is a code change (one place: `listings/
thresholds.py`), not a gold rebuild.

## Modern medallion variants

Research (June 2026) confirmed:
- Modern medallion writeups (Databricks, ml4devs, dataforest) describe
  bronze/silver/gold as the canonical 3 layers.
- For AI/ML workloads, a 4th "Platinum" or dedicated **Vector** layer
  is the emerging convention — vectors don't fit neatly into the
  bronze/silver/gold model because they consume from clean entities
  (silver) but are accessed via dedicated indexes (HNSW), so they sit
  alongside gold rather than inside it.

We chose to use "platinum" as the layer name to match the most common
modern usage. The concept is the same regardless of name.

## Sources

- [Beyond Bronze, Silver, Gold — Evolving the Medallion Architecture for the AI Era (Medium)](https://medium.com/@vishal.dutt.data.architect/beyond-bronze-silver-gold-evolving-the-medallion-architecture-for-the-ai-era-77d3cca78745)
- [Medallion Architecture: Bronze, Silver & Gold Layers Explained (2026 Guide)](https://dataforest.ai/blog/medallion-architecture)
- [What is medallion architecture (Databricks)](https://www.databricks.com/blog/what-is-medallion-architecture)
- [Medallion Architecture: Bronze, Silver, and Gold Data Layers (ml4devs)](https://www.ml4devs.com/what-is/medallion-architecture/)
