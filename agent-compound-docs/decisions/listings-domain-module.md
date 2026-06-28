# `listings/` — a neutral domain module

Decided 2026-06-15 during the search-perf refactor.

## Context

Before the refactor, the `Listing` ORM model, bucket label functions
(`bucket_noise`, `walk_minutes`), threshold constants, and per-listing
context dataclasses (`NearestSchool`, etc.) all lived under
`search/`. `chat/` and `core/` imported from `search/` to access these.
That meant the search module owned "what does noise=58 *mean*", which
is a presentation concern, not a search concern.

The asymmetry got worse with the addition of `ListingService` (direct
reads by ID for the HTTP endpoint + the agent's `open_listing` tool +
future bookmarks): three callers shouldn't depend on the *search*
module just to fetch a listing.

## Decision

Introduce `services/backend/src/flat_chat/listings/` — a leaf domain
module that owns shared listing concerns:

| Submodule | What it owns |
|---|---|
| `models.py` | `Listing`, `ListingGeoContext`, `ListingEmbedding` + the `ListingNearby*` junction ORMs (incl. `ListingNearbyKita`/`ListingNearbyLandmark`) + the `named_places` read-only Core `Table` (the gazetteer view backing `locate_place`) — all **read-only** views of the `world` schema (`{"schema": "world"}`; ingestion owns the DDL). `IronCard`/`RawListing` were removed in the schema-ownership split — the backend doesn't model iron/bronze. See [`schema-ownership-split.md`](schema-ownership-split.md). |
| `types.py` | `NoiseLabel`, `DensityLabel`, `GreeneryLabel`, `DistanceBucket`, `NearSpec`, `GtfsMode` Literal types (the MSS `MssStatus`/`MssDynamics` types were removed in geo-context v2) |
| `context.py` | `ListingDetail`, `UiApartment`, and all the nested dataclasses (`NearestTransitStop`, `NearestSchool`, …) |
| `labels.py` | `bucket_noise`, `bucket_density`, `bucket_greenery`, `walk_minutes`, `encode_modes`, `decode_modes`, `resolve_near_spec` |
| `thresholds.py` | The constants — noise dB cutoffs, density per-hectare cutoffs, greenery m² cutoffs, walking-distance ladder, per-dataset caps, GTFS mode code map |
| `service.py` | `ListingService` — async accessor for listings by ID (`get(id)`) or in batch (`get_batch(ids)`) |

Dependency rule: `listings/` imports from `core/` only. Nothing in
`listings/` imports from `chat/`, `search/`, or `api/`. The reverse is
fine — those layers depend on `listings/`.

## Layering, after the refactor

```
api/        HTTP routes only. Never touches search.
  │
chat/       Agent orchestration. Applies labels at conversion.
  │
search/     Filter + rank. Returns list[UiApartment]. Agent-only.
listings/   ORM, types, labels, thresholds, context, service.
  │
core/       DB, config, observability, deps.
```

## Why bucketing left `search/`

The same threshold table is needed in *two* directions:

- **Filter parsing** (`search/geo_filters.py`): user says `max_noise="quiet"`
  → SQL threshold `noise_total_lden < 55`. Reads `thresholds.py`.
- **Result projection** (`search/service.py` / `chat/tools.py`): raw
  value `58` → label `"lively"`. Reads `labels.py` which reads
  `thresholds.py`.

Both directions read the same numbers. If those numbers lived in
`search/`, every other consumer of "what does noise=58 mean" would have
to import from search — bookmarks, future export endpoints, partner
APIs. That's an inversion. Neutral module solves it.

## What stayed in `search/`

- Filter input shapes (`TransitFilter`, `SchoolFilter`, `HospitalFilter`,
  `KitaFilter`): these are search-input contracts, not listing data.
  They only exist because someone is *searching*.
- `SearchParams` + `SortBy`: same.
- `SearchService` itself: filter + rank is search's reason to exist.
- `PlaceService` (`search/places.py`): resolves a named place to a
  `place_ref` for `near_place_ref` search — agent-only, like `SearchService`.

## What got deleted

- `search/buckets.py` → moved to `listings/labels.py` + `listings/types.py`
- `search/distances.py` → moved to `listings/labels.py` + `listings/thresholds.py`
- `search/transit.py` → moved to `listings/labels.py` + `listings/thresholds.py`
- `search/geo_models.py` → no longer needed; gold replaces the per-table queries
- `search/geo_context_service.py` → 1000+ lines of LATERAL-chip + 12-query-detail-fan-out logic. The spatial work moves to gold ETL; the detail fetches move to `ListingService.get`.
- `search/models.py` → ORMs moved to `listings/models.py`

## Future shape

`listings/` is the natural home for:
- `BookmarkService` — when bookmarks land, it imports `ListingService`
  and writes to a `user_bookmarks` table. No new modules needed.
- Per-user listing preferences (saved searches, etc.)
- Shareable URL → listing resolver

Each of those imports from `listings/`; none of them import from `search/`.
