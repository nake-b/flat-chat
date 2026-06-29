# Travel-time (commute) search — engines, hosting, and the slice model

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
- **One active visualization channel.** Markers carry a single generic
  `channel_value` (replacing the old `price_warm_eur`), named once by
  `SessionState.marker_channel` (`{key,label}`, default `price_warm`). You only
  ever colour pins by one scalar, so there's no bag of channels. Backend ships
  semantics (`key`/`label`/`values`); the frontend owns the ramp in
  `state/channelStyles.ts` (`price_warm` → plain pins, `commute_min` → a
  green→red ramp). Same semantics/appearance split as overlays.
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
   annotates each marker's `channel_value` (minutes); if `max_minutes`, drops
   over-cutoff (filter preserves order, so `preview_cards` stays a true prefix);
   sets `marker_channel = commute_min`.

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
- **VBB freshness**: republished ~twice weekly; a stale `gtfs.zip` yields zero
  future-dated trips — re-run `prep-routing.sh` to refresh.
