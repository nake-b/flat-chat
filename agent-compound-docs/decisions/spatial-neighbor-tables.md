# Spatial neighbour tables — restoring old search precision

**Status:** Planned. No implementation yet. Sequenced for after `feat/gold-platinum-medallion-and-tests` lands on `main`.

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

## What this doesn't fix

Two related concerns share the same root cause (gold cached at write-time, search reads cached state) but **are NOT addressed by neighbour tables** and need separate fixes:

1. **`max_noise` NULL semantics**: gold's `noise_total_lden` is NULL for listings without a nearby noise sample. The old filter explicitly included NULL (`or_(subq IS NULL, subq < cutoff)`). The new filter is plain `noise_total_lden < cutoff`, which silently excludes NULL rows. Fix: revert to the optimistic `or_(is_(None), < cutoff)` predicate — one line. Separate concern; doesn't need a table.

2. **Stale-gold drift detector**: a new listing without a gold row is invisible to every geo filter (NULL predicates exclude it). The chain triggers (`silver.run` → `gold.run`, `geo_context.run` → `gold.run`) cover the standard daily flow, but ad-hoc workflows can land listings in silver without a gold row. Add a simple completeness probe to `/api/health` or as a startup log line:
   ```sql
   SELECT COUNT(*) FROM listings l
   LEFT JOIN listings_geo_context lgc ON lgc.listing_id = l.id
   WHERE l.location IS NOT NULL AND lgc.listing_id IS NULL;
   ```
   Non-zero = drift. Separate concern; doesn't need a table.

Also out of scope here: H3 / quadkey indexing (relevant only if we scale beyond Berlin), and refinement-cache integration of pandas (deferred per [`session-state-design.md`](session-state-design.md)).

## Implementation phases

This is the planned sequence. **No implementation yet** — this doc is the plan.

### Phase 1 — Schema + ETL for transit, schools, hospitals (priority families)

These three have the actual user-visible behaviour gaps.

- New migration `0006_spatial_neighbor_tables.py`:
  - `listings_nearby_transit` (`listing_id`, `stop_id`, `distance_m`, `modes`, `lines`, `name`, `rank` SMALLINT) with `(listing_id, distance_m)` B-tree, GIN on `modes`, GIN on `lines`.
  - `listings_nearby_schools` (`listing_id`, `school_id`, `distance_m`, `school_type`, `name`, `rank`) with `(listing_id, distance_m)` B-tree, B-tree on `school_type`.
  - `listings_nearby_hospitals` (`listing_id`, `hospital_id`, `distance_m`, `tier`, `name`, `rank`) with `(listing_id, distance_m)` B-tree, B-tree on `tier`.
- Drop now-redundant JSONB columns from `listings_geo_context`: `transit_top3`, `schools_top3`, `hospitals_top2`. Keep the chip scalars (`nearest_transit_m`, `nearest_transit_lines`, `nearest_transit_name`, etc.) — they remain useful for cheap unfiltered card rendering.
- New gold ETL functions in `services/ingestion/src/gold/enrich_listings.py`:
  - `enrich_nearby_transit` — `INSERT INTO listings_nearby_transit SELECT … FROM listings l, transit_stops ts WHERE ST_DWithin(…, 3000) OR rank <= 5`. Done per listing inside a `LATERAL`. Computes `rank` via window function ordered by distance.
  - `enrich_nearby_schools` — same shape, R=3000.
  - `enrich_nearby_hospitals` — same shape but R=10000 (people travel further to hospitals than to U-Bahn).
- Register each in `CHIP_FAMILIES` in `services/ingestion/src/gold/run.py` under new family names: `nearby_transit`, `nearby_schools`, `nearby_hospitals`. The existing `transit`/`schools`/`hospitals` families that fill the JSONB blobs go away.

### Phase 2 — Rewire filters to query the neighbour tables

- `services/backend/src/flat_chat/search/service.py:_apply_transit_filter` — replace the JSONB containment + nearest-only logic with EXISTS-against-`listings_nearby_transit` parameterised on `distance` / `modes` / `lines` / `stop_name`. Restores old-correct semantics.
- New `_apply_school_filter` reads from `listings_nearby_schools` and honours `f.distance` + `f.school_type`. The current catchment-membership check (`school_catchment IS NOT NULL`) stays as a separate filter path — keep both because they answer different questions:
  - **Catchment membership** = "what primary school does the kid at this address attend?" (legal/admin question; polygon containment).
  - **Nearest school by type** = "is there a Gymnasium / ISS / Berufsschule near here?" (proximity question; needs the neighbour table).
  - Surface both in `SchoolFilter`: keep `school_type` + `distance` (proximity) and add a separate `requires_catchment: bool = False` flag (catchment containment).
- `_apply_hospital_filter` reads from `listings_nearby_hospitals` and honours `f.distance` + `f.tier`. `tier="plan_hospital"` (default) is the emergency-care intent.

### Phase 3 — Update `ListingService.get` to assemble detail from neighbour tables

The detail panel currently reads `transit_top3` / `schools_top3` / `hospitals_top2` JSONB blobs. With those gone, it pulls the same shape via:

```python
nearby = await db.execute(
    select(listings_nearby_transit).where(
        listings_nearby_transit.c.listing_id == listing_id
    ).order_by(listings_nearby_transit.c.rank).limit(3)
)
```

Same Pydantic projection layer (`_parse_transit_top3` etc.) — just sourced from rows instead of JSONB. The Pydantic models in `listings/context.py` don't need to change.

### Phase 4 — Fix the two non-table concerns

These are quick and don't need a migration:

- **Noise NULL semantics**: change `_apply_noise_filter` predicate to `or_(noise_total_lden.is_(None), noise_total_lden < cutoff)`. Restores old optimistic-include behaviour.
- **Gold drift probe**: add a `check_gold_completeness()` async helper called from `/api/health`'s extended check, or emit as a startup log warning if non-zero. Implementation: a single `SELECT COUNT(*)` on the left-join.

### Phase 5 — Tests, docs

- Integration tests in `tests/integration/test_search_neighbour_filters.py`:
  - "near U8" matches a listing whose nearest stop is U1 but a U8 is 400m away (the case that fails today).
  - "near a Gymnasium" matches only listings with a Gymnasium in radius.
  - "near a plan_hospital" + tier='any' returns a superset of tier='plan_hospital'.
- Update [`gold-platinum-layers.md`](gold-platinum-layers.md) to mention the neighbour tables as a sibling concept to the chip scalars.
- Update [`geo-context-thresholds.md`](geo-context-thresholds.md) with the per-family R radius table (Transit=3km, Schools=3km, Hospitals=10km).
- Drop the "lost precision" caveats in this file once the rewire lands.

### Phases 6 (optional) — Parks / playgrounds / water

The current scalar chips (`nearest_park_m`, `playground.distance_m`, `water.distance_m`) answer the user-facing filter correctly because none of those filters takes attribute arguments. Only worth adding `listings_nearby_parks` etc. if a future filter wants "any park named X within Y meters" or "any Schwimmbad-class playground". Defer until that demand exists; revisit during the optional "filter UI / chips" workstream.

## Decision

**Approved by user** in the discussion that produced this doc (June 2026). Sequenced for after `feat/gold-platinum-medallion-and-tests` lands on `main`. Implementation lives on a follow-up branch; tests cover the regression direction so a future refactor can't silently re-narrow these filters.
