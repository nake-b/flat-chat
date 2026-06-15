# Spatial junction tables — restoring old search precision

**Status:** Implemented in `feat/spatial-junction-tables`. Migration 0006 + 6 junction tables + ETL + search rewire + tests landed June 2026.

The names "junction tables" / "POI features" / "scalar features" come from the discussion below and are now the canonical vocabulary in this codebase.

**Related docs:**
- [`gold-platinum-layers.md`](gold-platinum-layers.md) — the medallion layout this builds on
- [`geo-context-thresholds.md`](geo-context-thresholds.md) — the threshold numbers referenced here
- [`agent-vs-http-data-flow.md`](agent-vs-http-data-flow.md) — the CQRS split this preserves

## Problem

The gold-platinum refactor moved geo-context work from per-request `LATERAL` joins to ingest-time precomputation. Search dropped from "all spatial work, every call" to "B-tree predicates on a denormalised join." The trade was deliberate and the speed win is real.

But the chosen gold shape — a single chip scalar per family + a small JSONB blob of "top-K nearest" for detail rendering — silently narrowed several filters:

| Filter | Old (LATERAL EXISTS at query time) | Current gold shape | Effect |
|---|---|---|---|
| `transit.lines` | any stop within `f.distance` whose lines overlap | only the **nearest** stop's lines | Asking for "near U8" misses listings where the nearest stop is on U1 and a U8 stop is 50m further. |
| `transit.stop_name` | any stop in radius with name ILIKE | only the nearest stop's name | Same narrowing. |
| `transit.modes` | any stop in radius whose modes overlap | any of top-3 stops contains the mode | Slightly wider than old behaviour (~89/805 vs 23/805 in the test set), but a 4th-nearest U-Bahn falls off the list. |
| `school` | EXISTS school within `f.distance` (optionally typed) | `school_catchment IS NOT NULL` (catchment polygon membership) | Distance + school_type silently ignored. Berlin is fully tiled by primary catchments, so the filter is close to a no-op. |
| `hospital` | EXISTS hospital within `f.distance`, tier filter | `hospitals_top2 IS NOT NULL` (top-2 with no distance cap) | Distance + tier silently ignored. True for essentially every Berlin listing. |

The other filters (`near_park`, `near_playground`, `near_water`, `mss.*`, `density`) survive correctly because the precomputed scalar answers the user's actual question. The two scalar-but-with-issues are `max_noise` (NULL semantics flipped — separate fix) and `min_greenery` (cheap proxy upgraded to full composite — intentional improvement).

## The proposal

For each "near X" family with multi-attribute precision needs (transit, schools, hospitals — plus optionally parks/playgrounds/water if we want symmetry), add a **denormalised neighbour table**: one row per `(listing_id, feature_id)` pair within a generous radius.

```sql
-- Example: transit
CREATE TABLE listings_nearby_transit (
    listing_id UUID NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    stop_id    TEXT NOT NULL,
    distance_m INT  NOT NULL,
    modes      INT[] NOT NULL,
    lines      TEXT[] NOT NULL,
    name       TEXT,
    rank       SMALLINT NOT NULL,   -- 1..N within this listing, by distance
    PRIMARY KEY (listing_id, stop_id)
);
CREATE INDEX ON listings_nearby_transit (listing_id, distance_m);
CREATE INDEX ON listings_nearby_transit USING gin (modes);
CREATE INDEX ON listings_nearby_transit USING gin (lines);
```

**Population rule per listing** = `top-K=5` always-include ∪ `everything within R=3km`. Always-include guarantees the detail panel always has ≥5 rows to render even in transit-sparse periphery; within-R covers the filter use-case.

The search filter then becomes a cheap indexed join:

```sql
-- "near U-Bahn within 650m"
WHERE EXISTS (
  SELECT 1 FROM listings_nearby_transit nt
  WHERE nt.listing_id = l.id
    AND nt.distance_m <= 650
    AND nt.modes && ARRAY[400]::INT[]
)
```

A B-tree on `(listing_id, distance_m)` makes the range-scan touch ~20–200 candidate rows per listing in single-digit microseconds. Same answer as the old `ST_DWithin` EXISTS, zero spatial work at query time.

## Why this is the right shape

The pattern shows up under three names in three communities, all converging on the same conclusion: at scale, pre-materialise spatial joins.

### PostGIS / Crunchy / Boston GIS
PostGIS's KNN `<->` operator only hits the index when one side is a single literal — fine for "give me the nearest school to *this* listing", broken for "give me the nearest school to *each* listing in the result set" (a bulk spatial join). Both Crunchy and Boston GIS end their nearest-neighbour articles with "if you need this at scale, precompute it into a table."

- [Crunchy Data — A Deep Dive into PostGIS Nearest Neighbor Search](https://www.crunchydata.com/blog/a-deep-dive-into-postgis-nearest-neighbor-search) — explains the single-literal index limitation.
- [Carto — Bulk Nearest Neighbor using Lateral Joins](https://carto.com/blog/lateral-joins) — what we're currently doing in `search/service.py`; honest about the cost.
- [Boston GIS — Solving the Nearest Neighbor Problem in PostGIS](https://www.bostongis.com/PrinterFriendly.aspx?content_name=postgis_nearest_neighbor) — denormalisation as the production answer.
- [PostGIS workshop — Nearest-Neighbour Searching](https://postgis.net/workshops/postgis-intro/knn.html).
- [PostGIS distance operator <->](https://postgis.net/docs/geometry_distance_knn.html).

### Real estate
Zillow uses school **catchment polygons** (= our `school_catchment` column) for the legal "what school does this kid attend" question. For "near a feature", their architecture talk describes a move from on-demand spatial joins to ETL-precomputed serving tables — almost word-for-word our gold story.

- [Zillow Tech Hub — Zillow Transitions to Streaming Data Architecture](https://www.zillow.com/tech/streaming-data-architecture/).

### ML feature stores
The same pattern, abstracted: one row per entity, all aggregations / nearest-features precomputed, batch-refreshed, served with sub-millisecond latency at inference. "Cheat on inference latency by paying at training time" is the canonical example.

- [Databricks — What is a Feature Store?](https://www.databricks.com/blog/what-feature-store-complete-guide-ml-feature-engineering).
- [Assaf Pinhasi — Feature pipelines and feature stores deep dive](https://medium.com/data-for-ai/feature-pipelines-and-feature-stores-deep-dive-into-system-engineering-and-analytical-tradeoffs-3c208af5e05f).

### What we're not doing: H3 / hex cells
- [h3geo.org — Introduction to H3](https://h3geo.org/docs/) — hexagonal hierarchical spatial indexing.
- [Snowflake — Simplify Spatial Indexing with H3](https://www.snowflake.com/en/blog/getting-started-with-h3-hexagonal-grid/).
- [e6data — H3 vs Quadkey for spatial indexing](https://www.e6data.com/blog/geospatial-analytics-performance-bottleneck-h3-vs-quadkey-for-spatial-indexing).

H3 partitions the world into hexagonal cells; spatial joins become hash joins on cell ID. Sub-millisecond at any scale, but **inexact at cell boundaries** (res-9 hexes are ~150m across, so "within 200m" is fuzzy). Right answer if we ever go from Berlin to all-of-Germany; overkill for one city with exact bucket boundaries.

## Trade-offs

### Disk

Back-of-envelope for Berlin at 3km radius:

| Family | Avg within 3km | Rows/listing | Today (~850) | At 5000 (project cap) |
|---|---|---|---|---|
| Transit stops | ~300 | 300 | 256k | 1.5M |
| Schools | ~30 | 30 | 26k | 150k |
| Hospitals (3km) | ~6 | 6 | 5k | 30k |
| Parks (optional) | ~40 | 40 | 34k | 200k |
| Playgrounds (optional) | ~25 | 25 | 21k | 125k |
| Water (optional) | ~8 | 8 | 7k | 40k |

Row size ~120 B (UUID + INT + INT[] + TEXT[] + TEXT + SMALLINT). Total at the **5000-listing project cap**: ~2M rows, ~250 MB. Trivially safe. Going to 5–10 km radii for hospitals (which makes sense — people drive to hospitals further than they walk to U-Bahn) adds maybe 30% on hospitals alone, immaterial.

### Gold-rebuild time

Current `enrich_transit` does one `LATERAL ... LIMIT 3` per listing → ~853 LATERALs × ~5 ms ≈ ~4 s for the whole transit family. Neighbour-table version drops the LIMIT — every transit stop within 3km goes into the result set, then we `INSERT ... SELECT`. ~300k inserts on a fresh build, ~1–2 s in Postgres. Whole gold pipeline goes from ~30 s to maybe ~2 minutes at the project cap. Comfortably nightly-batch-friendly.

The chip scalars on `listings_geo_context` (`nearest_transit_m`, `nearest_park_m`, etc.) stay — they're a cheap summary derivable from `MIN(distance_m)` in the neighbour table. The JSONB top-K blobs go away (the neighbour table is the canonical source).

### Why not just `MATERIALIZED VIEW`?

Considered and rejected. A `MATERIALIZED VIEW` would work correctness-wise but loses per-listing UPSERT granularity — `REFRESH MATERIALIZED VIEW` re-runs the whole join. Today's chain-trigger pattern (silver.run UPSERTs only changed listings into gold) breaks. A plain table populated by an `enrich_nearby_X(conn)` function in the same style as the existing chip families keeps the per-listing rebuild fast and aligns with the established `--only` dispatch pattern.

### Why not stay with top-K JSONB?

The `lines` / `stop_name` / `school_type` / `hospital.tier` filters can't be expressed correctly against a fixed-K JSONB blob. Whatever K we pick, an attribute-driven question ("is there *any* U8 stop nearby, regardless of whether it's the closest?") is a multi-row question that a single chip + a tiny blob can't answer. The neighbour table is the smallest structural change that makes those filters correct.

## Resolved alongside (bundled into the same PR)

Two related concerns shared the same root cause (gold cached at write-time, search reads cached state). Both landed with this work:

1. **`max_noise` NULL semantics + 50 m gold-side gate**: gold's old `enrich_noise` had no distance cap, so `noise_total_lden` carried readings from kilometres away for bad-coords listings. Restored the gate at **50 m** (line-source attenuation: ~3 dB per doubling, so 50 m → 100 m drops ~3 dB; 50 m matches the standard mobile-noise-mapping aggregation radius per research literature). With the gate, NULL means "no trusted reading"; the search filter now uses `or_(IS NULL, < cutoff)` to optimistic-include those listings. Sources: [ScienceDirect — mobile noise mapping](https://www.sciencedirect.com/science/article/abs/pii/S0003682X14000693), [ScienceDirect — CNOSSOS-EU validation](https://www.sciencedirect.com/science/article/pii/S0003682X22000664), [MDPI — high-precision noise mapping](https://www.mdpi.com/2220-9964/11/8/441).

2. **Stale-gold drift detector**: `/api/health?extended=true` now surfaces `gold_orphans` — count of silver listings with no `listings_geo_context` row. Non-zero means silver ran but the gold chain didn't. Doesn't fail the health check; just exposes the number.

Still out of scope: H3 / quadkey indexing (relevant only if we scale beyond Berlin), and refinement-cache integration of pandas (deferred per [`session-state-design.md`](session-state-design.md)).

## What landed (June 2026)

| Layer | File | Change |
|---|---|---|
| Migration | `services/backend/alembic/versions/0006_spatial_junction_tables.py` | Six `listings_nearby_*` tables (transit / schools / hospitals / parks / playgrounds / water) with `PK(listing_id, feature_id)` + B-tree `(listing_id, distance_m)` + per-family attribute indexes (GIN on transit `modes` / `lines`, B-tree on school `school_type`, hospital `tier`). Drops 6 redundant JSONB columns from `listings_geo_context`. |
| ORM | `services/backend/src/flat_chat/listings/models.py` | Six `ListingNearby*` classes. |
| Gold ETL | `services/ingestion/src/gold/enrich_listings.py` | `enrich_nearby_*` (6 new families) populate `top-K=5 ∪ all-within-R` per listing. `enrich_chip_scalars` derives `nearest_transit_*` / `nearest_park_*` from the junction tables. `enrich_noise` gets the **50 m coverage gate** restored. |
| Search | `services/backend/src/flat_chat/search/service.py` | 6 `_apply_X_filter` methods now EXISTS-against the matching junction table; attribute filters (transit modes/lines/stop_name, school type, hospital tier) work end-to-end. `max_noise` uses `or_(IS NULL, < cutoff)`. |
| Search shape | `services/backend/src/flat_chat/search/geo_filters.py` | `SchoolFilter` gets `requires_catchment: bool = False` so the catchment-membership question and the proximity question coexist. |
| Detail panel | `services/backend/src/flat_chat/listings/service.py` | Per-junction-family top-N fetches replace the old JSONB-blob parses. Output Pydantic shapes unchanged. |
| Drift probe | `services/backend/src/flat_chat/main.py` | `GET /api/health?extended=true` returns `gold_orphans`. |
| Tests | `tests/integration/test_search_service.py`, `test_listing_service.py`, `test_search_null_geo_fields.py`, `tests/fixtures/factories.py` (6 new `nearby_*_row` helpers) | Junction-row-backed regression tests for every filter shape; optimistic-include test for `max_noise`. |

### Storage radii per family

| family | R |
|---|---|
| transit | 5 km |
| schools | 5 km |
| hospitals | 12 km |
| parks | 5 km |
| playgrounds | 3 km |
| water | 6 km |

Generous on purpose — search-time predicates (`distance_m <= resolve_near_spec(spec)`) do the actual cutoff. Disk math at the 5000-listing project cap: ~300 MB total across the six junction tables. Trivial.

### What stays scalar (chip columns on `listings_geo_context`)

Field-shaped facts that don't have a POI-set structure — one value per location:

- `noise_total_lden` (50 m gate restored)
- `persons_per_hectare`
- `mss_status`, `mss_dynamics`
- `school_catchment` (polygon-containment fact; combined with the school junction via `requires_catchment`)
- `greenery_profile.green_m2_within_300m` (composite aggregate)
- `disabled_parking_count`
- `nearest_transit_*` / `nearest_park_*` — denormalised summary derived from the junction tables by `enrich_chip_scalars`, used by the card-row projection for label rendering.
