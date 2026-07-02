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

## Rich candidate menu + curated university campuses (July 2026, issue #38)

Two related failures of "take rank #1" surfaced in live testing:

1. **Area vs point (#38).** "biking distance to Hasenheide" resolved to the night-bus stop *Hasenheide (Berlin)* (trigram sim 0.611) instead of *Volkspark Hasenheide* (the park, sim 0.344, buried). The bare-named point out-scored the park, and the agent had no signal that one is a bikeable *area* and the other a *point*.
2. **Universities.** "near TU/FU/HU" resolved to a random footprint among many same-named ALKIS rows (equal sim + equal `ST_Dimension` → arbitrary storage order → `ST_DWithin` against a random annex), and abbreviations fell below the 0.3 `%` threshold entirely (`similarity('HU','HU Berlin – Campus Mitte')=0.13`).

**Fix — make `locate_place` a rich disambiguation menu; the agent chooses.**

- **Rich candidates.** `PlaceCandidate` gains `geom_kind` (`area`/`line`/`point`, from `ST_Dimension`) and `locality` (containing Ortsteil, a correlated point-in-polygon against `world.ortsteile`). The tool renders `1. Volkspark Hasenheide — [park · area · Neukölln] place_ref=…`. Tool-protocol + docstring guidance tell the agent to pick the candidate matching intent (prefer an **area** for "near a green space"; **ask which** when several distinct campuses match) rather than taking #1.
- **Matching split.** `locate()` WHERE widens `name % q` with `name %> q` (word similarity) so short abbreviations/multi-word queries reach the candidate set. Word similarity is used **only to admit rows, never to rank** — it flattens to 1.0 for any name containing the query as a word (incl. out-of-Berlin VBB stops "Bernau, Hasenheide"). Ranking is `priority DESC → round(greatest(sim,wsim),1) DESC (coarse bucket) → ST_Dimension DESC (area beats coincident point) → exact score`. (`round` needs a `::numeric` cast — `round(real,int)` doesn't exist in Postgres; an integration test caught this.)
- **Curated universities (C + G5).** A new `world.curated_places` table (arm of `named_places`, `priority=1`; all other arms `priority=0`) holds editorially-curated campuses with **footprint-derived-but-frozen** geometry. `author_campuses.py` unions the real `landmarks` footprints matching a `name_pattern` ∩ `bbox` (editorial selectors in `campus_sources.yaml`) into a **MultiPolygon of the actual building footprints** (`ST_Union`, NOT a convex hull — a hull draws one fat blob that swallows streets/non-campus buildings and loosens `ST_DWithin`) and writes frozen WKT to `university_seed.yaml`; `_run_universities` loads it. Names embed both abbreviation and full form ("HU Berlin – Humboldt-Universität zu Berlin – Campus Nord") so word similarity matches "HU" *and* "Humboldt". **HU is authored as 3 campuses** (Mitte / Nord / Adlershof) so it stays a "which one?" question — geometry can't separate Nord from Mitte (~1.5 km apart) nor decide FU=Dahlem is "the campus"; that's editorial. Scope: TU, FU, HU×3, UdK, HTW, HWR, BHT, Charité (Mitte). ASH omitted — no footprint in `landmarks` to derive from.

**Why frozen, not a live union view (G5 not G2):** `landmarks.id` is an autoincrement serial reassigned on every geo-context reload, so an id-mapping rots; a live union view re-inherits per-ingest OSM/ALKIS noise with no human in the loop. Freezing the WKT makes the editorial call reviewable as a diff and stable at runtime. `PlaceService` still just queries the view — no runtime `ST_Union`, no dependency on live ids. Disambiguation is plain conversational clarification (the agent asks), **not** Pydantic AI's `ApprovalRequired`/`DeferredToolRequests` HITL — that's an action-approval gate, the wrong shape for parameter clarification (kept in reserve for future side-effecting tools like "book a viewing").

### What landed (July 2026)

| Layer | File | Change |
|---|---|---|
| Migration | `services/ingestion/alembic/versions/0009_curated_places.py` | `world.curated_places` table (+GIST/GIN trgm); recreate `named_places` with a curated arm + `priority` on every arm. |
| Authoring | `.../geo_context/campus_sources.yaml`, `author_campuses.py`, `university_seed.yaml` | Editorial selectors → footprint-union hull → frozen WKT (`python -m geo_context.author_campuses`). |
| Loader | `.../geo_context/extract/curated.py`, `run.py` (`_run_universities`, `--skip-universities`) | Load frozen seed into `curated_places` (idempotent replace). |
| Backend | `search/places.py`, `listings/models.py` | `geom_kind`+`locality` on `PlaceCandidate`; word-similarity WHERE; priority/bucket/dimension ORDER BY; `priority` column + `ortsteile` Table mapping. |
| Tool | `chat/tools/core.py` | Rich candidate rendering; protocol/phrase-map "pick the right one / prefer area / ask which campus". |
| Cleanup | `.../geo_context/landmark_seed.yaml` | Removed TU/FU/HU/Charité alias points (superseded by curated campuses). |
| Agent prose | `chat/agent.py` (`<surface_contract>` block), `chat/tools/core.py` | Stop leaking tool internals into chat ("Candidate #1 … place_ref …"): the agent names places/listings and never surfaces tool names, arguments, `place_ref`/IDs, or the candidate-N numbering. Exceptions: the user-visible 1-based card index, and stating the current search filters in plain words. `locate_place`'s candidate menu is marked internal. Light process narration ("let me look near it") stays allowed. |
| Lens anchor | `curated_places.anchor_lat/lon` → view `anchor_geom`; `search/places.py::anchor_point` | **Travel-time lens** routes to one point; the footprint centroid of a sprawling campus (FU spans 2.1 km) sat ~1 km from any building → surprising minutes. Curated campuses now carry a **main-building anchor** (`anchor_pattern` in `campus_sources.yaml` → `ST_PointOnSurface` frozen into the seed); `anchor_point` uses `COALESCE(anchor_geom, ST_Centroid(geom))` (FU routing point moves 878 m to the main building). **Distance lens unaffected** — `DistanceService` already measures `ST_Distance` to the full geometry (nearest point). |
| Tests | `tests/integration/test_place_service.py` | area-over-higher-scoring-point, locality, curated priority, abbreviation-via-word-similarity, 3-campus ambiguity. Plus a pre-existing `test_geocode` bootstrap fix (missing `pg_trgm`) + teardown hygiene. |

## Deferred

- **Pydantic AI deferred / on-demand tool loading** + a "skill" explainer for the `locate_place` → `search` flow — next PR.
- **OSM ingestion robustness** — Overpass is flaky; a Geofabrik Berlin extract fallback is the next step.
- **Materialised-view gazetteer** — only if the `named_places` UNION ever gets slow (it won't at Berlin scale).
- **Out-of-Berlin transit noise** — `transit_stops` includes VBB stops in Brandenburg (word similarity surfaces "Bernau, Hasenheide"); a Berlin-bbox filter on the transit arm would cut it.
