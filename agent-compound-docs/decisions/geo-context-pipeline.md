# Geo-context ingestion: design decisions

Decided 2026-06-13 during PR #8 review. Captures the rationale for why
`services/ingestion/src/geo_context/` looks the way it does so reviewers
don't have to re-litigate them.

> **Updated for geo-context v2 (June 2026):** the gold tier this doc lists as
> "deferred" now exists (see [`gold-platinum-layers.md`](gold-platinum-layers.md)
> + [`spatial-neighbor-tables.md`](spatial-neighbor-tables.md)). **MSS /
> Sozialmonitoring was removed entirely** on ethical grounds — its alias map and
> source table are gone. New named-place + admin layers (kitas, landmarks,
> bezirke/ortsteile, inner_city_zone) and the `world.named_places` view are
> documented in [`named-place-search.md`](named-place-search.md) and
> [`bezirk-ortsteil-resolution.md`](bezirk-ortsteil-resolution.md). `street_noise_2022`
> was renamed `strategic_noise_2022`.

## Why separate from `silver.run`

The first iteration of geo-context ingestion lived inside `silver.run` —
listings transform first, then context ingestion as a tacked-on second
step. We split it because the cadences are wildly different:

| Pipeline | Refresh frequency | Source size per run |
|---|---|---|
| Listings (silver) | daily, sometimes hourly | thousands of rows |
| GTFS | weekly | ~50MB zip → millions of stop_times |
| WFS context | yearly to monthly | a few MB per layer |

Bundling them meant: every daily listings run downloads 50MB of GTFS and
hits 13 WFS endpoints for no reason. Splitting means each pipeline runs
on its own schedule.

`geo_context` ships as its own compose profile (`--profile geo-context`)
that doesn't run on `docker compose up`. Scheduling is a deploy-layer
concern, intentionally left out of compose.

## Why English column names in the Transform stage (not in the migration)

We considered three options for the German→English problem:
- (a) Rename inside the migration's `CREATE TABLE` statements
- (b) Keep German names everywhere
- (c) German on the source side, English in a transform map

We picked **(c) with the migration creating English-named tables**. The
alias dict in `transform/aliases.py` is the single source of truth for
how each source field maps to a silver column. Benefits:

- Aliases are **testable** — `test_aliases.py` round-trips a sample row
  through every entry, asserting the rename is exhaustive.
- Aliases are **versioned independently** of the schema. If Berlin GDI
  renames `bsn` to `schulnr` next year, we patch the dict, not the DB.
- Migration is **declarative** about the final shape, not coupled to
  source-side naming conventions.

## Why GIST-only indexes (no attribute b-tree indexes)

The original migration added attribute b-tree indexes on `bezirk`,
`ortsteil`, `schluessel`, etc. — three per table on average. We dropped
all of them. Reasons:

- **Cardinality too low to help:** Berlin has 12 districts, a few hundred
  neighborhoods. A b-tree on `bezirk` against a 3M-row table is a
  sequential scan in disguise.
- **The agent's access pattern is spatial.** Every query the chat agent
  will write is of the form `ST_DWithin(table.geom, listing.location, m)`.
  GIST on `geom` covers it. Attribute filters happen *after* the spatial
  filter narrows the candidate set to ~tens of rows.
- **Cheap to add later.** When a real query latency problem surfaces, we
  add an index for that specific query. Easier to add than to drop.

For `transit_stops` we additionally GIST on `(geom::geography)` — same
pattern `0002_postgis_and_embedding_dim.py` uses for `listings.location`
— because `ST_DWithin(::geography, ::geography, meters)` is the typical
"nearest stop within walking distance" pattern.

## Why we collapse GTFS stops onto parent stations

GTFS publishes each U-Bahn platform as a separate `stops.txt` row.
Alexanderplatz alone has ~8 platform rows. For apartment search the
agent wants **one Alexanderplatz** with `modes_served = [1, 2]`, not 8
near-identical rows.

So in `transform/gtfs.py:build_stops` we:
- filter `location_type ∈ {0, 1}` (regular stops + parent stations,
  excluding entrances/exits which are `2`)
- compute `effective_id = COALESCE(parent_station, stop_id)`
- aggregate `modes_served` / `lines_served` / `wheelchair_boarding`
  over each `effective_id`
- emit one canonical row per `effective_id` — the parent station when
  there is one, else the standalone stop

`wheelchair_boarding` rolls up worst-case: any inaccessible child platform
makes the station report 2 (no). The reasoning is that someone in a
wheelchair shouldn't trust "accessible" if even one of their platforms is
not.

## What's iceboxed (and why)

`icebox/population_density_change_entw/` — the `_entw` year-over-year
change layer. Originally a separate table in the migration that was
**never populated** (it wasn't in `datasets_dict_slim`). Single-year
density delta is noisy at the planning-area level; the absolute
density (`population_density_2025`) is enough for the agent's purposes
right now. Kept in icebox because the schema + alias map are sound and
could be wired in quickly if year-over-year trend ever becomes useful.

## What's NOT here (and why)

- **Tree inventory** (3 layers, would require 3-way merge): superseded
  by `green_volume_2020` for the apartment use case.
- **GTFS frequency aggregation** (trips/hour, weekend service,
  first-/last-trip): expensive aggregation over 5M rows of `stop_times`,
  and apartment search doesn't actually need per-stop frequency.
  "Is there a U-Bahn nearby?" is enough.
- **GTFS transfers / agency / calendar_dates / per-trip shapes:**
  meta/edge-case data. We only need stops, routes, and one canonical
  line per (route, direction) for map rendering.
- **Gold tier** (`listing_geo_features` / `kiez_profile` pre-aggregated
  views): deferred. We don't yet know which features the chat agent
  will actually query, so building gold now would be premature. Live
  PostGIS spatial joins on indexed silver are sub-100ms at our scale.
  Revisit when (a) agent-tool p95 exceeds ~100ms, (b) we need cross-
  listing ranking, or (c) we ship per-Kiez UI cards.
- **Production scheduling:** wired triggerable, not wired scheduled.
  Cron lives at the deployment layer (host cron / cloud scheduler),
  not inside docker-compose.

## Open items / future work

- Re-evaluate **gold tier** once chat-agent tool usage is observed in
  prod. The minimal useful starter would be `listing_geo_features`
  with `nearest_transit_stop_id` + distance and `noise_at_location`,
  rebuilt nightly.
- Verify the **`wip`-status aliases** (kitas, landmarks, bezirke, ortsteile,
  umweltzone, water_bodies) against real source output on first prod run.
  They were derived from `DescribeFeatureType` but never sanity-checked
  against actual rows. (MSS was removed in geo-context v2.)
- **GTFS frequency aggregation** as a follow-up if "is this stop served
  on weekends?" or "how many trains per hour during rush?" turns out to
  matter to users.
- Replace the dead-stop noise dataset (`strategic_noise_2022`, renamed from
  `street_noise_2022` in geo-context v2) with the 2027 update when published.
