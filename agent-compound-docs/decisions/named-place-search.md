# Named-place search — `locate_place` → `near_place_ref` → geometry-precise `ST_DWithin`

**Status:** Implemented in `feat/geo-context-v2` (the PR #12 rework). Migration 0007 (ingestion) + `world.named_places` view + `PlaceService` + `locate_place` tool + `near_place_ref` search filter + tests landed June 2026.

**Related docs:**
- [`spatial-neighbor-tables.md`](spatial-neighbor-tables.md) — the junction-table pattern this deliberately does NOT reuse for named places
- [`geo-context-pipeline.md`](geo-context-pipeline.md) — the silver ingestion layer the source tables live in
- [`bezirk-ortsteil-resolution.md`](bezirk-ortsteil-resolution.md) — the sibling admin-area decision; "in Tiergarten" (Ortsteil) vs "near the Tiergarten" (park) is disambiguated across the two
- [`schema-ownership-split.md`](schema-ownership-split.md) — why the view is ingestion-owned (`world`) and the backend reads it read-only
- [`llm-tool-result-design.md`](llm-tool-result-design.md) — the tool-result-surface conventions `locate_place` follows

## Problem

A user says "apartments near TU Berlin", "by the Spree", "near the Brandenburger Tor", "near Schlachtensee", "around Körnerpark", "close to Charité". These are all the same shape of request — *proximity to one specific named place* — but they span wildly different geometries:

- a **campus polygon** (TU Berlin spreads over many hectares),
- a **river line** (the Spree is ~45 km of LINESTRING through the city),
- a **point monument** (Brandenburger Tor),
- a **lake polygon** (Schlachtensee),
- a small **park polygon** (Körnerpark),
- a **hospital footprint** (Charité).

Two naive approaches both fail:

1. **Centroid + radius.** Resolve the name to a single point, then `ST_DWithin(listing, centroid, r)`. Wrong for any extended feature: the centroid of the Spree sits in one spot on a 45 km line, so "near the Spree" would only match listings near that midpoint and miss everything along the rest of the river. Same failure for the TU campus — the centroid is one building; "near TU Berlin" should match the whole campus footprint.

2. **Reuse the junction tables.** The [spatial-neighbor-tables](spatial-neighbor-tables.md) pattern precomputes "near *any* of N features" by materialising one row per `(listing, feature)` pair. That's the right tool for *generic* "near a park" (you don't know which of ~1500 parks the user means, so you precompute all of them). It is the **wrong** tool for *one named* place: you'd be paying the N-features materialisation cost to answer a 1-feature question.

## Decision

One unified path for every named place:

```
locate_place(place_name="…")          # PlaceService → world.named_places, trigram
        ↓ returns ≤5 candidates, each with an opaque place_ref
agent picks the best candidate
        ↓
search_apartments(near_place_ref="<that place_ref>", radius_km=…)
        ↓ SearchService resolves the ONE geometry and runs
ST_DWithin(listing.location::geography, resolved_geom::geography, radius_m)
```

The key insight: **distance to ONE geometry is a single fast indexed query**, regardless of whether that geometry is a point, a line, or a polygon. `ST_DWithin` against a LINESTRING measures distance to the *nearest point on the line* — exactly right for "near the Spree". Against a polygon it measures distance to the *nearest edge* — exactly right for "near the TU campus". The junction's N-features cost only ever bit "near *any* of N parks"; it does not apply here.

### `locate_place` — `world.named_places`, an ingestion-owned VIEW

`locate_place` is backed by a plain SQL **view** (`world.named_places`), created in the ingestion `0007` migration:

```sql
CREATE VIEW world.named_places AS
    SELECT 'landmark' AS kind, id AS src_id, 'landmark:'||id AS place_ref, name, description, geom FROM landmarks
    UNION ALL SELECT 'park',     id, 'park:'||id,     name, NULL::text, geom FROM parks
    UNION ALL SELECT 'water',    id, 'water:'||id,    name, NULL::text, geom FROM water_bodies
    UNION ALL SELECT 'school',   id, 'school:'||id,   name, NULL::text, geom FROM schools
    UNION ALL SELECT 'kita',     id, 'kita:'||id,     name, NULL::text, geom FROM kitas
    UNION ALL SELECT 'hospital', id, 'hospital:'||id, name, NULL::text, geom FROM hospitals;
```

The view exposes `(kind, src_id, place_ref, name, description, geom)` and **composes the opaque `place_ref`** (`'<kind>:<src_id>'`, e.g. `"park:42"`). The view owns the table↔kind mapping; the backend never references the underlying table list.

**`PlaceService.locate(name)`** (`services/backend/src/flat_chat/search/places.py`) resolves names by `pg_trgm`:

```sql
SELECT place_ref, kind, name, description,
       ST_Y(ST_Centroid(geom)) AS lat, ST_X(ST_Centroid(geom)) AS lon
FROM   world.named_places
WHERE  name % :q                        -- trigram similarity operator
ORDER BY similarity(name, :q) DESC
LIMIT 5;
```

The `name % :q` predicate **pushes down into each UNION branch** and is served by per-base-table GIN trigram indexes (`landmarks_name_trgm`, `parks_name_trgm`, …, created in 0007). The `centroid` lat/lon are **for agent display only** — the actual search uses the full geometry, never the centroid.

### `near_place_ref` — geometry resolution stays format-only on the backend

`search_apartments(near_place_ref="park:42")` resolves the geometry in `SearchService._apply_listing_filters` (`search/service.py`):

```python
kind, src_id = _parse_place_ref(token)        # split on FIRST ':', require int id
geom_subq = (
    select(named_places.c.geom)
    .where(named_places.c.kind == kind, named_places.c.src_id == src_id)
    .scalar_subquery()
)
stmt = stmt.where(ST_DWithin(Listing.location::geography, geom_subq::geography, radius_m))
```

`_parse_place_ref` parses **only the token FORMAT** — split on the first `:`, require a non-empty kind and an integer `src_id` — with **zero knowledge of which tables back the view**. It is defensive by contract: any malformed token (no colon, empty kind, non-integer id, garbage) returns `None`, so the caller drops the filter and emits no `near_place_ref` predicate rather than a query that 500s. The LLM passes tokens opaquely, so a hallucinated token must fail closed.

At query time `kind` is a **constant**, so Postgres prunes the view's UNION to the one matching branch, and `src_id` hits that base table's PK. The "expensive-looking" view is one indexed single-row lookup.

### Division of labour: ALKIS + OSM

`landmarks` is the one named class with no pre-existing source table. It is fed from three sources:

- **ALKIS** (`alkis_gebaeude`, `source='alkis'`) — Berlin's official building cadastre. Richer than expected: Fernsehturm, Siegessäule, Reichstag, and TU Berlin all carry named footprints. ALKIS is the seed. **Generic-name filter:** the WFS transform (`_GENERIC_LANDMARK_NAMES` / `_GENERIC_LANDMARK_PATTERNS` in `transform/wfs.py`) drops footprints named after their *function* rather than a proper noun — 530× "Kindertagesstätte", 238× "Sporthalle", `Haus 7`, `zur Charité`, … (≈1,900 rows). These are useless gazetteer entries and flooded `locate_place`; the dedicated `kita`/`school`/`hospital` kinds already serve that intent. Exact-match only, so specific names that merely *contain* a generic word survive ("Kindertagesstätte Sonnenschein"). Proper-noun multi-polygons (9× "Technische Universität Berlin") are NOT yet de-duplicated — a noted follow-up needing a post-load pass.
- **OSM** (Overpass, `source='osm'`) — fills the *free-standing Bauwerk* gap that ALKIS's building-footprint model misses: Brandenburger Tor, the Olympiastadion bowl, bridges. Tags ingested: `historic=monument`, `man_made=tower|bridge`, `leisure=stadium`. `tourism=attraction` was **dropped** — in Berlin it was ~90% noise (zoo enclosures, garden micro-labels, art trivia); the genuinely-iconic attractions it surfaced moved to the curated **seed** instead. `historic=memorial` stays excluded (~99% Stolpersteine). Native geometry preserved (points, lines, polygons), appended after the ALKIS seed with `source='osm'` + a `category` derived from the tag.
- **Seed** (`landmark_seed.yaml`, `source='seed'`) — hand-curated iconic attractions (Gendarmenmarkt, Museumsinsel, Checkpoint Charlie, the Schlösser, …), informal Kieze, and abbreviation aliases (TU/HU/FU Berlin) that OSM/ALKIS miss or tag inconsistently. This is where the iconic `tourism=attraction` places now live, with verified geometry and descriptions.

No synthetic Brandenburger Tor: OSM provides it. `landmarks` stores **mixed geometry** (`geometry(Geometry, 4326)`) so points, lines, and polygons coexist in one table.

### Generic category filter vs named place — the boundary

| User says | Path | Why |
|---|---|---|
| "near **a** park / **a** lake / **a** kita / **a** school" | generic category filter (`near_park`, `near_water`, `kita`, `school`) → junction `EXISTS` | You don't know *which* feature — "any within distance" is the question. Precompute-all (junction) is correct and cheap. No name field. |
| "near **the** Tiergarten / **the** Spree / TU Berlin / Brandenburger Tor" | `locate_place` → `near_place_ref` → one-geometry `ST_DWithin` | You know *exactly* which feature — resolve it and measure to its real shape. |

The generic category filters deliberately carry **no name field**. Named-specific search is `locate_place`'s job. This keeps each filter doing one thing and avoids an arbitrary park-vs-building privilege split (see Rejected, below).

## Attribution

OSM is **ODbL**: the frontend keeps an explicit `© OpenStreetMap contributors` attribution (a MapLibre `AttributionControl` on the map — `MapPane.tsx`) wherever landmark data is surfaced. Berlin GDI / ALKIS data is `dl-de/zero-2-0` (no attribution required) or `dl-de/by-2-0` (attribution required) — see the source table in [`geo_context/README.md`](../../services/ingestion/src/geo_context/README.md).

## What was rejected

- **A materialised gazetteer table.** A `MATERIALIZED VIEW` (or a real table) populated from the source tables would need a refresh step and would go stale between refreshes. A **plain view** is always current, needs no storage, and — because the `name % :q` predicate pushes into each branch and hits per-base-table trigram indexes — is index-served at Berlin scale. Materialisation is only worth it if the UNION ever gets slow, which it won't at one city's worth of named places. (This *reverses* an earlier "no view, do the UNION in Python" idea: the view decouples the backend from the table list, which the Python version couldn't.)

- **Adding a `name` field to the generic category filters.** Earlier design let `near_park` carry an optional park name. Rejected: it created an arbitrary park-vs-building split (a tiny park got name-search privilege but TU Berlin didn't) and duplicated what `locate_place` already does uniformly. Named search is one path for all kinds.

- **A `Literal[...]` enum of place names** baked into the tool signature. Rejected: it can't scale to thousands of named features, goes stale the moment the data updates, and bloats the prompt. Free-text + trigram resolution is open-ended and data-driven.

- **Centroid handoff** (resolve to a point in `locate_place`, search by centroid radius). Rejected: wrong for every extended geometry (the Spree, any campus). The centroid is kept *only* for agent display ("I found the Spree at ~52.51, 13.39"); the search resolves the full geometry server-side via `near_place_ref`.

## What landed (June 2026)

| Layer | File | Change |
|---|---|---|
| Migration | `services/ingestion/alembic/versions/0007_geo_context_v2.py` | `world.named_places` VIEW; per-base-table GIN trigram indexes on `name`; `landmarks` table (mixed geometry, `source`+`category`). |
| OSM extract | `services/ingestion/src/geo_context/extract/osm.py` | Overpass query over Berlin, retry/backoff, `source='osm'` rows appended into `landmarks`. Geofabrik fallback = TODO. |
| ORM | `services/backend/src/flat_chat/listings/models.py` | `named_places` mapped as a read-only Core `Table` on dedicated metadata (excluded from `create_all` + the drift test). |
| Place service | `services/backend/src/flat_chat/search/places.py` | `PlaceService.locate(name)` — trigram resolution, ≤5 candidates, agent-only (like `SearchService`). |
| Search filter | `services/backend/src/flat_chat/search/service.py` | `near_place_ref` → scalar-subquery geometry resolution → `ST_DWithin`; `_parse_place_ref` (format-only, fail-closed). |
| Tool | `services/backend/src/flat_chat/chat/tools/core.py` | `locate_place` tool (pure lookup, no state mutation); `search_apartments` gains `near_place_ref`; `<tool_protocol>` documents the 2-tool flow; `<phrase_map>` distinguishes "in" vs "near the" Tiergarten. |
| Frontend | `services/frontend/src/components/MapPane.tsx`, `state/toolStatus.ts` | ODbL `AttributionControl`; `locate_place` status pill (`Locating … / Found …`). |
| Tests | `services/backend/tests/integration/test_place_service.py`, `test_search_service.py`, `tests/unit/test_place_ref_parse.py` | Trigram resolution; `near_place_ref` precise distance against an extended geometry; format-only token parsing incl. malformed input. |

## Deferred

- **Pydantic AI deferred / on-demand tool loading** + a "skill" explainer for the `locate_place` → `search` flow — next PR.
- **OSM ingestion robustness** — Overpass is flaky; a Geofabrik Berlin extract fallback is the next step.
- **Materialised-view gazetteer** — only if the `named_places` UNION ever gets slow (it won't at Berlin scale).
