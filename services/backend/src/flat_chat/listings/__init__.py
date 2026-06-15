"""Listings domain — shared concerns for listing data across all consumers.

Houses everything that's "about listings" without being specific to search
or to chat:
  - `models`: the `Listing` ORM (moved from `search/models.py`) + the gold
    (`ListingGeoContext`) and platinum (`ListingEmbedding`) ORMs.
  - `types`: the Literal-type vocabulary for chip labels, transit modes,
    and distance buckets. The Pydantic-AI tool surface uses these.
  - `context`: tier-3 detail Pydantic models (NearestTransitStop,
    NearestSchool, …, ListingContext) — the shape returned by
    `ListingService.get(id)` and stored in `SessionState.active_listing_detail`.
  - `labels`: bucket-label functions (`bucket_noise`, `walk_minutes`, …).
    Applied at the chat presentation layer when projecting raw gold values
    into `UiApartment` and LLM prose. Search calls these only for filter
    *parsing* (user "quiet" → threshold).
  - `thresholds`: numeric constants — single source of truth referenced by
    both labels and filter parsers. Each constant traces to a row in
    `agent-compound-docs/decisions/geo-context-thresholds.md`.
  - `service`: `ListingService` — async accessor for listings by ID
    (`get`) or in batch (`get_batch`). Used by the agent's `open_listing`
    tool, the HTTP `GET /api/listings/{id}` endpoint, and (future)
    bookmarks.

Dependency rule: this module is a *leaf* domain. It does not import from
`chat/`, `search/`, or `api/`. The reverse is fine — all of those depend
on `listings/`.

Architecture-decision doc: `agent-compound-docs/decisions/listings-domain-module.md`
"""
