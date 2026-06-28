# Bezirk / Ortsteil resolution — polygon-derived districts + OR-union with scraped freetext

**Status:** Implemented in `feat/geo-context-v2` (the PR #12 rework). Migration 0007 (ingestion) + `bezirke` / `ortsteile` polygon tables + `enrich_admin_areas` + the district OR-union in search landed June 2026.

**Related docs:**
- [`named-place-search.md`](named-place-search.md) — the sibling decision; "in Tiergarten" (Ortsteil, this doc) vs "near the Tiergarten" (park, that doc)
- [`geo-context-pipeline.md`](geo-context-pipeline.md) — the silver layer the `bezirke` / `ortsteile` polygons live in
- [`spatial-neighbor-tables.md`](spatial-neighbor-tables.md) — the gold ETL `enrich_*` pattern `enrich_admin_areas` follows

## Problem

A listing's district matters for search ("apartments in Mitte", "in Tiergarten", "in Friedrichshain-Kreuzberg") and for display. But the only district signal that arrives with a scraped listing is a **freetext string** (`Listing.district`) typed by whoever posted the ad. It is messy: inconsistent casing, abbreviations, marketing names ("Prenzlberg"), wrong administrative level (an Ortsteil where a Bezirk was expected, or vice versa), and often simply absent.

We *do* have precise coordinates for most listings — but those coordinates are the **source map-pin**, not a geocoded address. There is **no geocoding step** in this pipeline. So:

- listings **with a pin** can have their district derived geometrically, ignoring the freetext entirely;
- listings **without a pin** have *only* the freetext to fall back on.

Neither signal alone is reliable. The freetext is noisy; the pin is missing on a meaningful slice.

## Decision

### Ingest the admin polygons; assign by `ST_Covers` at gold time

Two ALKIS polygon layers land in silver (migration 0007, `world` schema):

- `bezirke` — Berlin's 12 borough boundaries. `name` holds the human label ("Mitte", "Friedrichshain-Kreuzberg"); `bezirk_id` holds the numeric id.
- `ortsteile` — the finer locality boundaries (Tiergarten, Prenzlauer Berg, …).

At gold time, `enrich_admin_areas` (`services/ingestion/src/gold/enrich_listings.py`) assigns each listing's polygon-derived Bezirk and Ortsteil by point-in-polygon:

```sql
-- per listing, smallest covering polygon wins (defensive against overlaps)
SELECT name FROM bezirke
WHERE ST_Covers(geom, listing.location)
ORDER BY ST_Area(geom) ASC
LIMIT 1;
```

The results are denormalised onto `listings_geo_context` as `listing_bezirk` / `listing_ortsteil`. (`ST_Covers`, not `ST_Within`, so a pin exactly on a boundary still resolves.)

### Bezirke WFS fix

The original PR #12 had two bezirke bugs that are fixed here:

- **Layer name**: `alkis_bezirke:bezirksgrenzen` (not `:bezirke`).
- **Column aliases**: `{"namgem": "name", "name": "bezirk_id"}` — the layer's `name` column is a numeric id, and `namgem` is the human label. Aliasing them straight through gave numeric ids where district *names* were expected. A regression test now asserts the values are human names ("Mitte"), not numeric ids.

### District filter — OR-union across three sources

The search district filter does **not** pick one source. It **OR-widens** across all three:

```python
# search/service.py — params.districts
for d in params.districts:
    pattern = _escape_for_substring(d)
    district_clauses.append(Listing.district.ilike(pattern))          # scraped freetext
    district_clauses.append(lgc.listing_bezirk.ilike(pattern))        # ALKIS Bezirk polygon
    district_clauses.append(lgc.listing_ortsteil.ilike(pattern))      # ALKIS Ortsteil polygon
stmt = stmt.where(or_(*district_clauses))
```

Why the union rather than "prefer the polygon, fall back to freetext":

- **Robust to messy freetext.** "in Tiergarten" matches whether the poster labelled the listing "Mitte" (the Bezirk), the Ortsteil polygon resolved to "Tiergarten", or both. We don't have to reconcile the poster's idea of the district with the official one — we accept either.
- **Robust to missing pins.** A pin-less listing has no `listing_bezirk` / `listing_ortsteil`, but its scraped `Listing.district` still participates in the OR. A pinned listing whose freetext is garbage still matches on the polygon assignment. Each source covers the other's blind spot.
- **Bezirk vs Ortsteil level-agnostic.** Because both polygon columns are in the OR, the user can name either administrative level ("Mitte" the Bezirk, or "Tiergarten" the Ortsteil inside it) and get sensible matches without the agent having to know which level a name belongs to.

### Display — coalesce to one value

For single-value display (card chips, detail panel), the polygon-derived value is preferred and the scraped freetext is the fallback: `COALESCE(listing_ortsteil, listing_bezirk, district)`-style resolution. The OR-union is a *search* behaviour (cast a wide net); display wants one clean label.

### The no-geocoding reality (recorded explicitly)

There is **no address geocoding** anywhere in this pipeline, and this decision is the reason the OR-union exists rather than a cleaner "geocode the address, derive everything" approach:

- Coordinates are **source map-pins** — supplied by the listing source, not derived from the address string. They're good when present.
- Scraped freetext `district` is the **pin-less fallback** — the only district signal for listings the source didn't pin.

If a geocoding step is ever added, it would feed the same `listing_bezirk` / `listing_ortsteil` columns and the OR-union would keep working unchanged.

## "Inside the ring" provenance (Umweltzone)

The `inside_ring` filter is related (it's the other polygon-membership fact landed in this work) and its provenance is worth recording alongside the admin areas:

- **"Inside the ring" = the Umweltzone polygon**, ingested as `inner_city_zone`. Berlin's legal low-emission zone (Umweltzone) is bounded almost exactly by the S-Bahn ring — the shape Berliners call the *Hundekopf* ("dog's head"). It is published as **one clean WFS feature**, so we ingest it directly.
- `enrich_inside_ring` sets `inside_ring` via `ST_Contains(inner_city_zone.geom, listing.location)`.
- It is **NOT** derived from GTFS S41/S42 rail geometry — the rail centerline doesn't close into a clean polygon. The Umweltzone is the pragmatic, legally-grounded stand-in.
- Berlin is **polycentric** (Mitte, City West around Zoo, several Kiez hubs) — there is no single "city center". The agent is instructed (`agent.py` `_city_center_block`) to interpret "city center" / "Zentrum" / "Innenstadt" as *inside the ring* rather than guessing one neighbourhood. The `<phrase_map>` in `tools.py` maps those phrases to `inside_ring: true`.

(The literal S-Bahn rail-ring polygon from GTFS S41/S42 is a deferred nice-to-have if the Umweltzone approximation ever proves insufficient.)

## What was rejected

- **Geocoding the scraped address** to derive a precise point, then deriving district from that. Out of scope: no geocoder is wired, and the source pins are good enough where present. The OR-union absorbs the resulting messiness instead.
- **"Prefer polygon, ignore freetext."** Rejected because it loses every pin-less listing from district searches. The freetext is noisy but it's the only signal those listings have.
- **"Trust the scraped `district` only."** Rejected for the inverse reason — it discards the precise, consistent polygon assignment we can compute for pinned listings, and inherits all the freetext mess.

## What landed (June 2026)

| Layer | File | Change |
|---|---|---|
| Migration | `services/ingestion/alembic/versions/0007_geo_context_v2.py` | `bezirke` (`name` + `bezirk_id` + MultiPolygon) and `ortsteile` polygon tables; `listing_bezirk` / `listing_ortsteil` columns on `listings_geo_context`. |
| WFS aliases | `services/ingestion/src/geo_context/{datasets.yaml,transform/aliases.py}` | bezirke layer `alkis_bezirke:bezirksgrenzen`; alias `{"namgem":"name","name":"bezirk_id"}`. |
| Gold ETL | `services/ingestion/src/gold/enrich_listings.py` | `enrich_admin_areas` (`ST_Covers`, smallest-polygon-wins) + `enrich_inside_ring` (`ST_Contains` against `inner_city_zone`). |
| Search | `services/backend/src/flat_chat/search/service.py` | District filter OR-union across `Listing.district ∪ listing_bezirk ∪ listing_ortsteil`; `inside_ring` strict-equality predicate. |
| Display | `services/backend/src/flat_chat/listings/{context.py,service.py}`, `services/frontend/src/components/CardDetail.tsx` | `listing_bezirk` / `listing_ortsteil` surfaced on detail; coalesced label; `inside_ring` yes/no + a `⭕ inside ring` card chip. |
| Agent | `services/backend/src/flat_chat/chat/agent.py`, `chat/tools.py` | `_city_center_block` (polycentric Berlin → ring); `<phrase_map>` "in" vs "near the" Tiergarten, "city center" → `inside_ring`. |
| Tests | `services/backend/tests/integration/test_search_service.py`, `services/ingestion/tests/integration/test_gold_enrichers_v2.py` | District OR-union; `inside_ring`; `enrich_admin_areas`; bezirke human-names regression. |
