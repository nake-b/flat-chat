# Result-set facets — grounding the agent's whole-set summaries

Decided 2026-06-28.

## Context

After a search the agent writes orienting prose like:

> "33 apartments within 1 km of Mauerpark — a nice mix of Prenzlauer Berg and
> Wedding, ranging from very affordable to around €1,950/month. Most are quiet…"

Per turn the LLM only sees `<current_state>` (`total` / `loaded` / `order` /
`filters`) plus the top `PREVIEW_N`=10 cards rendered by
`LlmResultSetView.summary()`. So while the **count** is solid, the **price
ceiling**, **neighbourhood mix**, and **area range** were extrapolated from a
sample of 10 — a soft violation of the agent's `<honesty>` rule. The data to
back those claims either wasn't surfaced (`result_markers` carries every match's
`price_warm_eur` but only to the map, never as LLM prose) or didn't exist at all
for the full set (district and area are not on markers).

## Decision

Compute aggregate **facets** over the *whole filtered set* at search time, store
them on `SessionState.facets`, and surface a compact `<result_facets>` block in
the per-turn prompt. The agent grounds whole-set claims in that block; the listed
cards remain "the top few", not the basis for set-wide statements.

`ResultFacets` (in `search/schemas.py`):
- `price_warm_eur`, `area_sqm`: `NumericFacet` (min / median / max)
- `districts`: `list[DistrictCount]` — count per **Ortsteil**, busiest first

`SearchService._facets` runs two cheap aggregate queries, both reusing
`_apply_listing_filters` + `_apply_geo_context_filters` so they describe exactly
the rows the markers/preview do:
1. one-row numeric: `min` / `percentile_cont(0.5)` / `max` for rent and area;
2. `GROUP BY listing_ortsteil` count (NULL Ortsteil excluded).

Surfaced via `_result_facets_block` in `chat/llm_context.py`; an `<honesty>` line
in `chat/agent.py` tells the agent to use it for whole-set summaries.

## Why these choices

- **Compute in SQL, not from in-memory markers.** Price *could* be derived from
  `result_markers` for free, but (a) markers are capped at `MARKER_CAP`=5000, so
  the range would silently describe the 5000, not the true set; and (b) area and
  district aren't on markers at all. SQL aggregates are exact past the cap and
  cover every column. Rejected: deriving price in Python from markers.

- **District facet = Ortsteil**, not the scraped `Listing.district` or the
  Bezirk. Ortsteil ("Prenzlauer Berg", "Wedding") is the neighbourhood
  granularity Berliners — and the user's own example — name areas in, and it's
  the clean ALKIS-polygon assignment. Listings without a polygon assignment are
  excluded, so district counts can sum to **< total**; the numeric facets still
  cover every match.

- **Two queries, sequential.** The shared `AsyncSession` is one asyncpg
  connection — concurrent `execute` on it is unsafe, so no `asyncio.gather`.
  Both are B-tree aggregates over the already-filtered gold set (single-digit ms);
  search goes from ~3 round-trips to ~5. If this ever shows up in profiling, the
  escape hatch is to make `_facets` best-effort (return `None` on error/timeout)
  — the prose degrades gracefully to the pre-facets behaviour.

- **LLM-only consumer.** `facets` rides the AG-UI state snapshot (frontend
  deserialization is loose and ignores unknown fields) but is **not rendered** in
  the UI. The TS mirror (`state/SessionState.ts`) and `SessionStateResponse`
  carry it for type-parity / OpenAPI accuracy and a future results-header UI.

## Rejected / deferred

- **Card-strip header UI** for facets — deferred; the agent is the consumer that
  motivated this. The TS types are in place for when it's wanted.
- **Greenery / density / rooms facets** — not included; price, district, area
  cover the prose that was being fabricated. Easy to add as more `NumericFacet` /
  bucket columns on the same two queries if needed.
- **Fattening `result_markers`** with district/area to compute facets client-side
  — rejected: inflates the columnar wire payload at 5k markers for data only the
  aggregates need.

## Files

`search/schemas.py` (models), `search/service.py` (`_facets`, 4-tuple return),
`chat/session_state.py` + `chat/schemas.py` (`facets` field + response mirror),
`chat/tools.py` (plumb), `chat/llm_context.py` (`<result_facets>` render),
`chat/agent.py` (honesty line), `frontend/src/state/SessionState.ts` (TS types).

Tests: `tests/integration/test_search_service.py` (executes the aggregates —
ranges, district counts, filter-honouring, NULL-Ortsteil exclusion, zero-result
→ `None`), `tests/unit/test_session_state.py` (round-trip),
`tests/unit/test_llm_context.py` (block rendering).
