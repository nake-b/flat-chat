"""CoreCapability — the core listing tools: search, open, page, locate_place.

The backbone of the agent's tool surface (the other capabilities — overlays,
lenses — decorate the result set this produces). Carries the `<tool_protocol>`
prose + phrase map for these tools; the cross-capability invariants live in
`backbone.py:TOOL_BACKBONE`.
"""

import logging
from dataclasses import dataclass
from datetime import date

from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AgentToolset

from flat_chat.chat.llm_context import LlmResultSetView
from flat_chat.chat.state import ChatDeps
from flat_chat.chat.tools.emission import StateEmittingToolset
from flat_chat.chat.tools.lenses import reapply_lens_hook
from flat_chat.chat.tools.overlays import rebuild_search_overlays_hook
from flat_chat.listings.types import (
    DensityLabel,
    GreeneryLabel,
    NearSpec,
    NoiseLabel,
)
from flat_chat.search.geo_filters import (
    HospitalFilter,
    KitaFilter,
    SchoolFilter,
    TransitFilter,
    WaterFilter,
)
from flat_chat.search.schemas import SearchParams, SortBy

logger = logging.getLogger(__name__)

toolset: FunctionToolset[ChatDeps] = FunctionToolset()

# Name of the search tool (Pydantic AI derives it from the `search_apartments`
# function name below). Both the live and reload "finish-collapse" paths key on
# it (issue #22) — `chat/service.py` and `api/chat.py` import this so a rename of
# the tool is one edit here, not three literals drifting apart.
SEARCH_TOOL_NAME = "search_apartments"


# Pydantic AI parses each tool's docstring with `griffe` and lifts the `Args:`
# bullets directly into the JSON schema sent to the LLM. The docstring IS the
# schema description — no need (and no benefit) to wrap params in
# `Annotated[..., Field(description=...)]`; that would double up the source of
# truth. Keep arg descriptions in the docstring `Args:` section.
#
# The numeric thresholds in this prose (distance ladder, noise/greenery/density
# cutoffs) are written out literally. They MUST match `listings/thresholds.py`,
# which is the single source of truth the SQL filters read. The match is guarded
# by `test_search_tool_docs_match_thresholds` — it reads the constants and
# asserts each appears in the right param description, so tuning a constant
# without updating this prose fails CI loudly.


_CORE_PROTOCOL = """\
<tool_protocol>
Core tools for finding and inspecting listings (the backbone rules — one result
set, 1-based indices, the place_ref flow — are in <tool_backbone>):
  - `search_apartments(...)` — run or REPLACE the active result set.
  - `locate_place(place_name=...)` — resolve a SPECIFIC named place (a landmark,
    park, lake/river, school, kita, hospital, transit station) to candidate
    references. Returns a numbered list, each with an opaque `place_ref`.
  - `open_listing(indices=[k])` — open the detail panel for listing #k AND attach
    the neighbourhood-context blob (transit, schools, kitas, parks, landmarks,
    noise, hospitals). Pass `indices=[k, m, …]` for side-by-side comparison
    prose; UI focus anchors to the first index. NEVER pass UUIDs, external IDs,
    or anything that isn't a 1-based number visible on the cards.
  - `get_result_page(page=N)` — browse beyond the top 5. CSV format. Indices in
    the CSV are absolute (1..N of the whole result set), not page-local.

Named-place search — the 2-tool flow. When the user names a SPECIFIC place
("near TU Berlin", "near the Spree", "by the Brandenburger Tor", "near
Schlachtensee"): call `locate_place`, pick the best candidate (ask only if
genuinely ambiguous), then `search_apartments(near_place_ref="<place_ref>",
radius_km=…)` — this matches the place's EXACT shape (a river line, a campus
polygon), which a coordinate radius cannot.

After `open_listing(indices=[k])`, ALWAYS write a 1–2 sentence highlight of what
stands out (transit, noise, neighbourhood character) — the detail panel renders
structured data; your reply calls out what matters. Don't stay silent after the
tool completes.
</tool_protocol>

<phrase_map>
Templates for translating user phrases into `search_apartments` arguments:
  - "near U-Bahn"               → transit: {modes: ["u_bahn"]}
  - "on U8" / "served by U8"    → transit: {lines: ["U8"], distance: "very_near"}
  - "S+U Wittenau" / "near
    Wittenau station"           → transit: {stop_name: "Wittenau"}
  - "within 5 min walk of an
    S-Bahn"                     → transit: {modes: ["s_bahn"], distance: 400}
  - "quiet" / "quiet street"    → max_noise: "quiet"
  - "leafy" / "lots of
    greenery"                   → min_greenery: "leafy"
  - "park nearby"               → near_park: "near"
  - "family-friendly" /
    "good for kids"             → near_park: "near", near_playground: "near",
                                  kita: {distance: "near"}, max_noise: "quiet"
  - "near a kita" / "daycare
    nearby"                     → kita: {distance: "near"}
  - "near a Grundschule"        → school: {school_type: "Grundschule"}
  - "near a lake"               → near_water: {kinds: ["lake"]}
  - "near a river" /
    "by the canal"              → near_water: {kinds: ["river"]}
  - "by the water" /
    "waterfront"                → near_water: {distance: "near"}
  - "inside the ring" /
    "innerhalb des Rings" /
    "city center" / "central" /
    "Innenstadt" / "Zentrum"    → inside_ring: true
  - "outside the ring"          → inside_ring: false
  - "in Tiergarten" (the Ortsteil,
    i.e. the neighbourhood)     → districts: ["Tiergarten"]
  - "near the Tiergarten"
    (the park itself)           → locate_place("Tiergarten") → near_place_ref
  - "near TU Berlin" /
    "by the Spree" /
    "near Brandenburger Tor" /
    "near Schlachtensee"        → locate_place("…") → near_place_ref
  - "arty / queer-friendly /
    nightlife / loft vibe"      → query: "<the user's words>"
</phrase_map>
"""


@toolset.instructions
def tool_protocol_instructions() -> str:
    """Toolset-scoped guidance: how to use these tools, with a phrase map.

    Pydantic AI appends this after `agent.instructions` when composing the
    system prompt — co-locating tool guidance with the tool implementations
    means renaming a tool is one atomic edit (function name + this text).
    """
    return _CORE_PROTOCOL


@toolset.tool
async def search_apartments(
    ctx: RunContext[ChatDeps],
    query: str | None = None,
    # Money
    price_warm_min: float | None = None,
    price_warm_max: float | None = None,
    price_cold_max: float | None = None,
    # Size
    rooms_min: float | None = None,
    rooms_max: float | None = None,
    bedrooms_min: int | None = None,
    area_sqm_min: float | None = None,
    area_sqm_max: float | None = None,
    # Location
    districts: list[str] | None = None,
    near_lat: float | None = None,
    near_lon: float | None = None,
    near_place_ref: str | None = None,
    radius_km: float = 2.0,
    inside_ring: bool | None = None,
    # Building / availability
    floor_min: int | None = None,
    floor_max: int | None = None,
    listing_type: str | None = None,
    available_by: date | None = None,
    # Amenities (tri-state: leave unset = don't filter)
    wbs_required: bool | None = None,
    is_furnished: bool | None = None,
    has_balcony: bool | None = None,
    has_kitchen: bool | None = None,
    has_elevator: bool | None = None,
    has_images: bool | None = None,
    # Geo-context (transit / schools / kitas / parks / noise / ring / ...)
    transit: TransitFilter | None = None,
    school: SchoolFilter | None = None,
    hospital: HospitalFilter | None = None,
    kita: KitaFilter | None = None,
    near_water: WaterFilter | None = None,
    near_park: NearSpec | None = None,
    near_playground: NearSpec | None = None,
    max_noise: NoiseLabel | None = None,
    min_greenery: GreeneryLabel | None = None,
    density: DensityLabel | None = None,
    sort_by: SortBy = "relevance",
) -> str:
    """Search for apartments in Berlin. Replaces the current result set.

    Berlin renters search structurally — by warm rent, rooms, district, WBS,
    move-in date, amenities. Use as many filters as the user has given you;
    leave the rest unset.

    Args:
        query: Free-text semantic match (title + description) for subjective
            intent no filter below captures ("arty", "queer-friendly",
            "nightlife"); it ranks within the structured filters, so combine
            them. Omit it for purely structural searches.

        price_warm_min: Minimum warm rent in euros (warm = incl. Nebenkosten).
        price_warm_max: Maximum warm rent in euros.
        price_cold_max: Maximum cold rent in euros (Kaltmiete only — without
            Nebenkosten).

        rooms_min: Minimum total rooms. In Germany "Zimmer" includes the
            living room — `rooms_min=2` matches "2-Zimmer-Wohnungen".
        rooms_max: Maximum total rooms.
        bedrooms_min: Minimum bedrooms (Schlafzimmer) — separate from
            `rooms` because the German count includes the living room.
        area_sqm_min: Minimum living area in square meters.
        area_sqm_max: Maximum living area in square meters.

        districts: Berlin district or neighborhood names to restrict to.
            Substring match — both Bezirke ("Mitte", "Friedrichshain-Kreuzberg",
            "Pankow", "Charlottenburg-Wilmersdorf", "Spandau", "Steglitz-Zehlendorf",
            "Tempelhof-Schöneberg", "Neukölln", "Treptow-Köpenick",
            "Marzahn-Hellersdorf", "Lichtenberg", "Reinickendorf") and Ortsteile
            ("Kreuzberg", "Prenzlauer Berg", "Wedding", "Schöneberg", ...) work.
        near_lat: Latitude for proximity search.
        near_lon: Longitude for proximity search.
        near_place_ref: Opaque reference to ONE named place, obtained from
            `locate_place`. Restricts results to listings within `radius_km`
            of that place's exact geometry (line/polygon-precise — correct
            for rivers and campuses). NEVER invent this token; only pass a
            `place_ref` that `locate_place` returned this conversation. For
            generic "near a park/lake/kita" use the category filters instead.
        radius_km: Search radius in km (used with near_lat/near_lon AND with
            near_place_ref).
        inside_ring: Berlin "inside the ring" (the S-Bahn ring ≈ the
            Umweltzone low-emission zone — Berlin's closest thing to a
            "city centre"). True = only listings inside the ring, False =
            only outside, unset = don't filter. Map "city center" /
            "central" / "Innenstadt" / "Zentrum" to True.

        floor_min: Minimum floor number (0 = Erdgeschoss).
        floor_max: Maximum floor number.
        listing_type: Optional raw listing-type filter (data is not yet
            normalized — values vary by source, e.g. "Etagenwohnung",
            "1 Room Flat"). Leave unset unless the user explicitly names one.
        available_by: Latest acceptable move-in date (ISO `YYYY-MM-DD`).
            Matches listings whose `available_from` is on or before this date.
            Pydantic parses the string into a `date` automatically; bad
            formats trigger a tool-retry with a clear error.

        wbs_required: Berlin Wohnberechtigungsschein (WBS) filter. Set to
            True if the user wants WBS-restricted listings (e.g. they hold a
            WBS), or False if they want to exclude them. Leave unset if the
            user hasn't mentioned WBS.
        is_furnished: True for möbliert only, False for unmöbliert only,
            unset for both. Furnished listings are typically short-term.
        has_balcony: True = must have Balkon, False = must not have.
        has_kitchen: True = must have Einbauküche / Pantryküche.
        has_elevator: True = must have Aufzug.
        has_images: If true, exclude listings without images. Default (None)
            returns all listings — leave unset unless the user explicitly
            asks for photos only.

        transit: Filter by proximity to public transit. Pass as an object
            like `{"modes": ["u_bahn"], "distance": "near"}`. Fields:
              - `distance`: how close — one of `"next_to"` (≤150m),
                `"very_near"` (≤400m), `"near"` (≤650m, default),
                `"walking_distance"` (≤1200m), `"bike_distance"` (≤2500m),
                or an int (meters).
              - `modes`: which service types must be reachable, any of
                `"u_bahn"`, `"s_bahn"`, `"tram"`, `"bus"`, `"ferry"`,
                `"regional"`, `"mainline"`. OR semantics (any-of).
              - `lines`: specific line names like `["U8", "S5"]` — match
                stops whose `lines_served` contains any of these.
              - `stop_name`: substring match on stop name (e.g.
                `"Wittenau"` matches "S+U Wittenau").
            Examples: "near U-Bahn" → `{"modes": ["u_bahn"]}`. "On U8" →
            `{"lines": ["U8"], "distance": "very_near"}`. "5 min walk from
            S-Bahn" → `{"modes": ["s_bahn"], "distance": 400}`.

        school: Filter by proximity to a school. Pass as
            `{"distance": "near"}` for "near a school", or
            `{"school_type": "Grundschule"}` to require a primary school
            (Berlin Schulverzeichnis categories — "Grundschule", "Gymnasium",
            "ISS", "Berufsschule"; free-text substring match). Example:
            "Grundschule nearby" → `{"school_type": "Grundschule"}`.

        hospital: Filter by proximity to a hospital. Pass as
            `{"distance": "walking_distance"}`. `tier` defaults to
            `"plan_hospital"` (the Krankenhausplan emergency-care network —
            what users usually mean); use `"any"` to include specialty
            clinics too. Example: "hospital nearby" →
            `{"distance": "walking_distance"}`.

        kita: Filter by proximity to a daycare (Kita). Pass as
            `{"distance": "near"}`. Kitas have no sub-type, so distance is
            the only field. Example: "near a kita" → `{"distance": "near"}`.
            For a SPECIFIC named kita ("near Kita Sonnenschein") use
            `locate_place` → `near_place_ref` instead.

        near_water: Require a water body within `distance`. Pass as an object
            like `{"kinds": ["lake"], "distance": "near"}`. Fields:
              - `distance`: same `NearSpec` ladder as `transit.distance`.
              - `kinds`: narrow by type, any of `"lake"` (standing water —
                Seen/Teiche), `"river"` (flowing water — rivers, canals, the
                Spree), `"harbor"` (Hafen). OR semantics (any-of). Omit for
                ANY water. Berlin canals count as `"river"` (there is no
                separate canal category in the data).
            Examples: "near a lake" → `{"kinds": ["lake"]}`. "by the water" →
            `{"distance": "near"}`. For a SPECIFIC named water ("near the
            Wannsee") use `locate_place` → `near_place_ref` instead.

        near_park: Require a non-cemetery park within this distance.
            Same `NearSpec` ladder as `transit.distance` — `"next_to"` /
            `"very_near"` / `"near"` / `"walking_distance"` /
            `"bike_distance"`, or an int. Example: "park nearby" →
            `"near"`.

        near_playground: Require a playground within this distance.
            Same ladder. Example: "playground for the kids" → `"near"`.

        max_noise: Maximum Lden noise level. `"quiet"` (< 55 dB, WHO
            health-threshold) or `"lively"` (< 65 dB, normal urban band).
            Example: "quiet street" → `"quiet"`.

        min_greenery: Minimum greenery level (WHO Europe rule: ≥0.5 ha
            green within 300m = leafy; ≥1 ha within 300m = very_leafy).
            `"leafy"` or `"very_leafy"`. Example: "leafy
            neighbourhood" → `"leafy"`.

        density: Population density bucket. `"sparse"` (<50 persons/ha,
            suburban feel), `"moderate"` (50-150, typical urban European),
            `"dense"` (≥150, inner-city Kreuzberg/Neukölln norm).

        sort_by: "relevance" (requires query — otherwise falls back to
            recent), "price", "area", or "recent".
    """
    params = SearchParams(
        query=query,
        price_warm_min=price_warm_min,
        price_warm_max=price_warm_max,
        price_cold_max=price_cold_max,
        rooms_min=rooms_min,
        rooms_max=rooms_max,
        bedrooms_min=bedrooms_min,
        area_sqm_min=area_sqm_min,
        area_sqm_max=area_sqm_max,
        districts=districts,
        near_lat=near_lat,
        near_lon=near_lon,
        near_place_ref=near_place_ref,
        radius_km=radius_km,
        inside_ring=inside_ring,
        floor_min=floor_min,
        floor_max=floor_max,
        listing_type=listing_type,
        available_by=available_by,
        wbs_required=wbs_required,
        is_furnished=is_furnished,
        has_balcony=has_balcony,
        has_kitchen=has_kitchen,
        has_elevator=has_elevator,
        has_images=has_images,
        transit=transit,
        school=school,
        hospital=hospital,
        kita=kita,
        near_park=near_park,
        near_playground=near_playground,
        near_water=near_water,
        max_noise=max_noise,
        min_greenery=min_greenery,
        density=density,
        sort_by=sort_by,
    )

    # Execute the search. SearchService drops null-coordinate listings and
    # returns markers (EVERY match, ≤ MARKER_CAP), the top-N preview cards,
    # the total, and whole-set facets (price/area ranges, neighbourhood counts).
    result = await ctx.deps.search_service.search(params)

    # SessionState is the canonical in-memory snapshot. Both the LLM (via
    # build_dynamic_state_prompt) and the frontend (via the AG-UI state
    # stream) read from here. One representation, two consumers.
    ctx.deps.state.search_params = params
    ctx.deps.state.apply_search_result(result)
    # A new search resets the user's focus (a lens re-derivation keeps it).
    ctx.deps.state.active_id = None
    ctx.deps.state.active_listing_detail = None

    # Post-search hooks (same chat layer, ordered): redraw the geometry this
    # search is anchored to (the Spree, the U7) so results are shown IN RELATION
    # to it, then re-apply the active lens (if any) so a refinement keeps its
    # heatmap/filter. Each hook owns its own error policy — `reapply_lens_hook`
    # swallows a routing outage and drops the lens rather than failing the search.
    await rebuild_search_overlays_hook(ctx)
    await reapply_lens_hook(ctx)

    # State mutation above auto-emits a STATE_SNAPSHOT via StateEmittingToolset;
    # the tool just returns prose for the LLM.
    return LlmResultSetView(ctx.deps.state).summary(ctx.deps.state.preview_cards)


@toolset.tool
async def locate_place(ctx: RunContext[ChatDeps], place_name: str) -> str:
    """Resolve a SPECIFIC named place to candidate references.

    Use this ONLY when the user names a specific place — a landmark
    ("Brandenburger Tor", "TU Berlin", "Siegessäule"), a named park
    ("Tiergarten", "Görlitzer Park"), a named lake/river ("the Spree",
    "Schlachtensee"), a named school/kita, or a named hospital ("Charité").
    Do NOT use it for generic proximity ("near a park", "near a lake") —
    those are category filters on `search_apartments` (`near_park`,
    `near_water`, `kita`, `school`).

    Returns a short numbered list of candidates, each with an opaque
    `place_ref`. Pick the best one and pass its `place_ref` to
    `search_apartments(near_place_ref="…", radius_km=…)`, which matches
    listings against that place's exact geometry. If several candidates fit
    and the choice matters, ask the user which they meant.

    This is a PURE LOOKUP — it does not change the result set or the map.

    Args:
        place_name: The place name to look up, in the user's words (German
            or English). Substring/fuzzy match — partial names are fine.
    """
    candidates = await ctx.deps.place_service.locate(place_name)
    if not candidates:
        return (
            f'No place named "{place_name}" found. Try a different spelling or a '
            "broader name; otherwise fall back to a district filter (districts=[…]) "
            "or a generic category filter (near_park / near_water / kita)."
        )

    lines = [f'Candidates for "{place_name}" (pick one place_ref):']
    for i, c in enumerate(candidates, start=1):
        bits = [c.name or "(unnamed)", f"[{c.kind}]"]
        if c.description:
            bits.append(c.description)
        coords = (
            f" @ {c.lat:.4f},{c.lon:.4f}"
            if c.lat is not None and c.lon is not None
            else ""
        )
        lines.append(f"  {i}. {' — '.join(bits)}{coords}  place_ref={c.place_ref}")
    lines.append('Then: search_apartments(near_place_ref="<place_ref>", radius_km=…).')
    return "\n".join(lines)


# TODO(post-MVP): split into a pure-query `get_listing_prose` + pure-command
# `select_listing` / `pan_map_to` pair, called in parallel by the LLM, once
# the Generative-UI pattern-3 frontend tools land. See CLAUDE.md "Deferred /
# nice-to-have" → "Parallel tool-call patterns for split commands."
@toolset.tool
async def open_listing(
    ctx: RunContext[ChatDeps],
    indices: list[int],
) -> str:
    """Open a listing's detail panel AND return its full info.

    Dual purpose by design: this is both a data-fetch (returns prose so the
    LLM can reason and write a highlight) and a UI command (sets
    `active_id`, opens the right-hand detail panel, attaches the
    neighbourhood-context blob for the Neighbourhood-context UI block).

    Listings are referenced by their 1-based index in the current result
    set — the same number the user sees on each card. Indices are stable
    until the next `search_apartments` call.

    Single-index call (`indices=[k]`) opens the detail panel for listing #k
    AND attaches the neighbourhood-context blob (transit, schools, kitas,
    parks, landmarks, noise, hospitals). Multi-index calls (`indices=[k,
    m, …]`) anchor UI focus to the first index but return prose for all; no
    geo-context fetch (use it for side-by-side comparison).

    Args:
        indices: 1-based positions referring to the most recent search/page
            output. NEVER pass UUIDs, external IDs, or anything that isn't a
            simple 1-based number visible to the user.
    """
    markers = ctx.deps.state.result_markers
    if not markers:
        return "No active search results. Run search_apartments first."
    if not indices:
        return "Pass at least one 1-based index, e.g. open_listing([1])."

    rs = LlmResultSetView(ctx.deps.state)
    preview = ctx.deps.state.preview_cards

    # Clear unconditionally on entry so a stale blob from a prior call
    # doesn't leak into a multi-index / out-of-range response.
    ctx.deps.state.active_listing_detail = None

    # Anchor the detail panel to indices[0] regardless of count. Indices
    # resolve against the marker order (the canonical result set).
    first = indices[0]
    pos = first - 1
    if 0 <= pos < len(markers):
        ctx.deps.state.active_id = markers[pos].id
        # Single-index calls fetch tier 3 via ListingService and store it
        # in state. The LLM reads this via build_dynamic_state_prompt's
        # `<user_focus>` block on the next prompt build — so there's no
        # need to embed the detail prose in this tool's return value.
        if len(indices) == 1:
            detail = await ctx.deps.listing_service.get_detail(markers[pos].id)
            if detail is not None:
                ctx.deps.state.active_listing_detail = detail

    # Resolve a tier-2 card for each requested index (for the prose). The
    # hot preview covers the top-N; anything beyond hydrates on demand by
    # marker id.
    need_ids = [
        markers[i - 1].id
        for i in indices
        if 0 <= i - 1 < len(markers) and (i - 1) >= len(preview)
    ]
    hydrated = (
        {c.id: c for c in await ctx.deps.listing_service.get_cards(need_ids)}
        if need_ids
        else {}
    )
    items = []
    for i in indices:
        p = i - 1
        if not (0 <= p < len(markers)):
            items.append((i, None))
        elif p < len(preview):
            items.append((i, preview[p]))
        else:
            items.append((i, hydrated.get(markers[p].id)))

    # active_id / active_listing_detail mutations above auto-emit a
    # STATE_SNAPSHOT via StateEmittingToolset; just return the prose.
    return rs.detail(items)


@toolset.tool
async def get_result_page(
    ctx: RunContext[ChatDeps],
    page: int = 1,
    page_size: int = 10,
) -> str:
    """Show a compact page of the current result set.

    Does NOT mutate state (no snapshot emitted); it hydrates the page's cards
    on demand by marker id (the preview covers page 1). The agent uses this to
    peek beyond the top-5 shown by the initial `search_apartments` summary.

    Args:
        page: 1-based page number.
        page_size: Listings per page (default 10).
    """
    markers = ctx.deps.state.result_markers
    if not markers:
        return "No active search results. Run search_apartments first."

    rs = LlmResultSetView(ctx.deps.state)
    total = rs.total
    total_pages = max(1, (total + page_size - 1) // page_size)
    if page < 1 or page > total_pages:
        return (
            f"Page {page} is out of range. There are {total} results "
            f"({total_pages} pages of {page_size})."
        )

    start = (page - 1) * page_size
    if start >= len(markers):
        # Beyond the markers we materialised (only possible past MARKER_CAP).
        return (
            f"Page {page} is beyond the {len(markers)} listings loaded on the "
            "map. Refine your search to narrow it down."
        )
    end = min(start + page_size, len(markers))

    preview = ctx.deps.state.preview_cards
    if end <= len(preview):
        cards = preview[start:end]
    else:
        ids = [m.id for m in markers[start:end]]
        cards = await ctx.deps.listing_service.get_cards(ids)

    return rs.page(
        cards, start=start, page=page, total_pages=total_pages, page_size=page_size
    )


@dataclass
class CoreCapability(AbstractCapability[ChatDeps]):
    """The always-loaded backbone tools bundled as a v2 capability:
    `search_apartments`, `open_listing`, `get_result_page`, `locate_place`.

    `locate_place` lives here (not with overlays/lenses) because it mints the
    `place_ref` tokens that every other capability consumes, so it must always be
    loaded. Map-overlay tools are in `MapOverlayCapability`, lens tools in
    `LensCapability` — see `agent-compound-docs/decisions/capability-landscape.md`.

    Returns the toolset wrapped in `StateEmittingToolset` so any `deps.state`
    mutation a tool makes auto-emits a STATE_SNAPSHOT to the frontend — emission
    stays structural (the wrapper intercepts `call_tool`), not something each
    tool body has to remember. See `state_emission.py` and `map-overlays.md`.
    """

    def get_toolset(self) -> AgentToolset[ChatDeps] | None:
        return StateEmittingToolset(toolset)
