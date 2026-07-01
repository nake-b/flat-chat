# Travel-time (commute) search — engines, hosting, and the slice model

> **Update (PR #41 review).** The travel lens was generalized into a proper
> **lens layer** and the routing service was hardened. What changed vs. the text
> below (kept for the design history):
> - **Lens layer.** The travel-time "lens" is now one case of a general
>   abstraction — see [`lens-layer.md`](lens-layer.md). `SessionState.travel_time_filter`
>   → `active_lens: TravelTimeLens | DistanceLens` (discriminated union in the leaf
>   `listings/lenses.py`); the shared `_apply_travel_lens` → generic
>   `chat/tools/lenses.py:_apply_lens` driven by a provider registry. A second lens
>   (bird's-eye **distance**, PostGIS `ST_Distance`, no engine) proves the split.
> - **Tool rename.** `apply_travel_time` → **`apply_travel_time_lens`** (+ new
>   `apply_distance_lens`), moved into `LensCapability` — see
>   [`capability-landscape.md`](capability-landscape.md).
> - **Routing client split.** The 390-line `routing/service.py` became a thin
>   `OsrmClient` (`routing/osrm.py`) + `MotisClient` (`routing/motis.py`) +
>   `RoutingService` orchestrator. `RoutingError` moved to `routing/errors.py`.
>   The module-level feed-window cache + `_reset_feed_window_cache` test hook are
>   gone — the TTL cache is now **instance state on `MotisClient`** (fresh client =
>   clean cache); the health check reaches it via `Depends(get_routing_service)`.
>   `_parse_metrics_window` / `_commute_departure` / `feed_window_stale` live on
>   `routing/motis.py`; departure/stops are DTOs (`CommuteDeparture`,
>   `ReachableStop`), the anchor is `listings.context.Anchor`.
> - **SSOT constants.** The hand-rolled `_haversine_m` → `listings/geo.py:equirect_distance_m`
>   (last-mile loop only); `_WALK_SPEED_M_PER_MIN` deleted in favour of
>   `thresholds.PEDESTRIAN_M_PER_S`; the walk cap is `thresholds.CAP_LAST_MILE_WALK_M`
>   (1500 m, up from a hard-coded 1000).
> - **Walk-time** (a real street-network travel mode) is a separate follow-up:
>   issue #49.

## Context

Conversational apartment search needs "find me a place I can actually commute
from" — rank/filter listings by **travel time** to a place the user cares about
(work, uni, a landmark), for **car** and **public transit**. This records what
was built and why. Engine research/benchmarks live in
[`../conversations/travel-time-and-transit-apis.md`](../conversations/travel-time-and-transit-apis.md).

## Engines — chosen, with evidence

Both were stood up in Docker and benchmarked against the real Berlin OSM extract
+ real VBB GTFS:

| | Engine | Why |
|---|---|---|
| **Car** | **OSRM** `/table` matrix | 117 MiB RAM, 13 s preprocess; one anchor→many in a single call (~0.5 ms/listing) |
| **Transit** | **self-hosted MOTIS** `one-to-many` | ~300 MiB RAM; isochrone primitive 15–21 ms; OSRM has no transit |
| **Rejected: Transitous** (public MOTIS) | dev-only | its ToS forbids routing/isochrone load + commercial use |

Both engines and our `world.transit_stops` come from the **same VBB GTFS**, so
stop IDs align (matters for the phase-2 hub join). Endpoint quirks (verified):
OSRM coords are **lon,lat**, MOTIS **lat;lon**; durations are **seconds**
(we expose minutes); OSRM default `--max-table-size` is 100 (we run 5500).

## Hosting — two internal services, opt-in profile

`osrm` + `motis` are internal compose services (no published port; backend
reaches them by hostname). They sit behind the **`routing` profile** because
they need a one-time data prep (`scripts/prep-routing.sh` builds the graphs into
the bind-mounted `data/routing/{osrm,motis}` dirs) — like the out-of-band tiles
file. Without the profile the backend still runs; `apply_travel_time` **degrades
gracefully** (returns a "couldn't reach the routing service" message and the
agent offers to proceed). Promoting to default-on is possible later by baking
prep into an init container.

## The slice model — why a separate tool, separate channel

Travel time is **its own concern**, kept orthogonal to the existing slices
(filters / markers / overlays from [`map-overlays.md`](map-overlays.md)):

- **Separate tool `apply_travel_time`**, not a `search_apartments` argument. The
  dividing line is **failure domain**: SQL-over-gold filters are deterministic
  and always available; routing is a *fallible external service*. As its own
  tool a routing failure is isolated and the agent degrades gracefully, instead
  of half-failing a mega-search. (Drawn around the *concept* — hub lookups in
  phase 2 are SQL but still flow through this tool for one mental model.)
- **One active visualization lens.** Markers carry a single generic
  `lens_value` (replacing the old `price_warm_eur`), named once by
  `SessionState.marker_lens` (`{key,label}`, default `price_warm`). You only
  ever colour pins by one scalar, so there's no bag of channels. Backend ships
  semantics (`key`/`label`/`values`); the frontend owns the ramp in
  `state/lensStyles.ts` (`price_warm` → plain pins, `commute_min` → a Berlin-red
  sequential ramp, vibrant-near → pale-far, with a frontend-computed adaptive
  domain). Same semantics/appearance split as overlays.
- **Independence + one-way seeding.** Slices are independent SSOTs; a travel
  filter deterministically *seeds* the channel (and an isochrone-style anchor
  overlay), which the agent can still set independently — exactly the existing
  `search → auto-overlay` pattern. "Heatmap commute but don't filter" and
  "≤30 min, which auto-colours" are both expressible.
- **Hover detail is NOT on the wire.** Markers stay thin (id, lat, lng, one
  scalar); rich tooltip data hydrates lazily via `GET /api/listings?ids=`.

## Flow

`apply_travel_time(near_place_ref, mode, max_minutes?)` (agent calls
`locate_place` first for the ref):
1. `PlaceService.anchor_point(ref)` → label + centroid coords.
2. set `state.travel_time_filter`; draw the anchor as a pinned overlay.
3. shared `_apply_travel_lens` → `RoutingService.resolve(markers, filter)`
   annotates each marker's `lens_value` (minutes); if `max_minutes`, drops
   over-cutoff (filter preserves order, so `preview_cards` stays a true prefix);
   sets `marker_lens = commute_min`.

`search_apartments` calls the same `_apply_travel_lens` at the end, so a
refinement **re-applies the lens** instead of reverting to price. The lens
persists in `SessionState` (reload-recoverable) until the user changes it.

## Phase 2 (not yet built) — precompute hubs

For a fixed set of common anchors, precompute travel time into a
`world.listings_travel_time` junction table via gold ETL (OSRM `/table` + MOTIS
`one-to-all` joined to `transit_stops` by the shared `stop_id`).
`RoutingService.resolve` would serve known-hub anchors from SQL (instant,
session-durable) and fall back to the live engines otherwise — invisible to the
tool and the slice model.

## Known limits

- **Schedule-based, no live traffic** — fine for ranking; stated to users.
- **OSRM big tables**: 1×5000 ≈ 2.5 s worst case; route only the narrowed set,
  or switch OSRM to CH for large matrices. We chunk destinations (90/req).
- **VBB freshness**: republished ~twice weekly; the loaded feed carries a short
  service calendar, so the window lapses and future-dated trips vanish. The
  backend clamps + labels departures against the MOTIS `/metrics` window rather
  than silently returning nothing; re-run `prep-routing.sh` to refresh the
  window. See §Freshness above.

## Freshness — the feed window is authoritative (clamp + label)

MOTIS loads a **finite** VBB timetable window (`prep-routing.sh` sets
`first_day: TODAY, num_days: 365`, but the downloaded feed carries a short
service calendar, so the *effective* window is only a week or so). Once real
time passes that window, a naive "next weekday 08:00" departure lands **outside**
it → MOTIS returns ~0 reachable stops → every listing's `lens_value` is null →
all-grey pins. This shipped once and was diagnosed as pure feed-staleness (the
lens/card/detail code was correct).

The fix has two halves:

- **The backend knows which dates have data.** MOTIS exposes the loaded window
  on its Prometheus `/metrics` endpoint —
  `nigiri_timetable_first_day_timestamp_seconds{tag="vbb"}` /
  `..._last_day_...` (unix seconds). `routing/service.py:fetch_transit_feed_window`
  reads + parses these (Berlin-local dates, ~5-min TTL cache; failures uncached
  so recovery is instant), and `RoutingService.feed_window()` shares one cache
  with the health endpoint. This is the **authoritative** source — no GTFS
  calendar parsing, no `world.transit_feed_info` table.
- **Clamp + label, don't fail.** `_commute_departure(window)` clamps the
  representative weekday-morning departure into `[first, last]` (rolling to the
  last in-window weekday when the feed has lapsed, the first when the window is
  future) and returns `(iso, stale, as_of_date)`. `_motis` stamps
  `TravelTimeFilter.schedule_stale` / `schedule_as_of` from it; when stale,
  `apply_travel_time` appends a "schedule only runs through <date>" note to its
  prose and `LensLegend` shows "schedule as of <date>". A slightly-old schedule
  is still useful (transit changes slowly) — so we clamp + inform rather than
  refuse. When MOTIS is unreachable / the gauges are absent, `feed_window()` is
  `None` and departure falls back to the plain next-weekday 08:00 (never crash).
  Car (OSRM) is date-independent, so none of this touches driving.

`GET /api/health?extended=true` surfaces `transit_feed: {first_day, last_day,
stale}` (null if MOTIS is unreachable) so ops can spot a lapsed feed.

**Keeping it fresh is manual for the MVP.** Re-running `prep-routing.sh`
re-downloads the VBB feed, rebuilds the graph with `first_day: TODAY`, and
restarts the engines (so the re-import takes effect without a second manual
step). Scheduling this is a documented TODO (root `CLAUDE.md`): no ETL is
scheduled for the MVP, and when it is, routing refresh should be its **own**
nightly host-cron job — not folded into the listings ETL (different cadence,
different failure domain).

## Shipped follow-ups (same PR)

- **B′ — transit stops in the gazetteer (migration 0008).** `locate_place` now
  resolves arbitrary S/U-Bahn/tram/bus stations as a deduped `transit_stop` arm
  of `world.named_places` (`GROUP BY name` + centroid; trgm index on
  `transit_stops.name`). `src_id` became TEXT view-wide (GTFS `stop_id`s are
  colon-laden), with the matching ripple in `listings/models.py`,
  `_parse_place_ref` (split on first `:`), and an `overlay_geometry` `kind`-guard
  that draws the station point rather than snapping to a nearby footprint. So an
  anchor no longer has to be a curated landmark (the earlier "only Alexanderplatz
  resolves because it's also a seed landmark" gap is closed).
- **Lens-aware clusters.** Cluster bubbles colour by the mean lens value under an
  active lens (MapLibre `clusterProperties` `sum_value`/`n_valued` over the shared
  `lensStyles` ramp), so the heatmap no longer drowns in red-by-count at city
  zoom. The commute ramp is a Berlin-red sequential (vibrant red = near → pale =
  far) over a frontend-computed adaptive domain, to match the rest of the marker
  design. Hexbin/H3 is the deferred next-level view (see root `CLAUDE.md`
  Deferred section).
