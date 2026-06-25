from datetime import date

from ag_ui.core import EventType, StateSnapshotEvent
from pydantic_ai import FunctionToolset, RunContext, ToolReturn

from flat_chat.chat.llm_context import LlmResultSetView
from flat_chat.chat.state import ChatDeps
from flat_chat.listings.types import (
    DensityLabel,
    GreeneryLabel,
    NearSpec,
    NoiseLabel,
)
from flat_chat.search.geo_filters import (
    HospitalFilter,
    MssFilter,
    SchoolFilter,
    TransitFilter,
)
from flat_chat.search.schemas import SearchParams, SortBy

toolset: FunctionToolset[ChatDeps] = FunctionToolset()


# Pydantic AI parses each tool's docstring with `griffe` and lifts the `Args:`
# bullets directly into the JSON schema sent to the LLM. The docstring IS the
# schema description — no need (and no benefit) to wrap params in
# `Annotated[..., Field(description=...)]`; that would double up the source of
# truth. Keep arg descriptions in the docstring `Args:` section.


_TOOL_PROTOCOL = """\
<tool_protocol>
There is ONE active result set per conversation. Listings are referenced by
1-based indices into it — the same numbers shown on the card strip.
Indices are stable until the next `search_apartments` call.

Tools:
  - `search_apartments(...)` — run or REPLACE the active result set. To
    refine, call again with ALL filters you want to keep (omitted args are
    dropped). Never volunteer a filter the user did not explicitly ask for.
  - `open_listing(indices=[k])` — open the detail panel for listing #k AND
    attach the neighbourhood-context blob (transit, schools, parks, noise,
    MSS, hospitals). Pass `indices=[k, m, …]` for side-by-side comparison
    prose; UI focus anchors to the first index. NEVER pass UUIDs, external
    IDs, or anything that isn't a 1-based number visible on the cards.
  - `get_result_page(page=N)` — browse beyond the top 5. CSV format.
    Indices in the CSV are absolute (1..N of the whole result set), not
    page-local.

After `open_listing(indices=[k])`, ALWAYS write a 1–2 sentence highlight
of what stands out (transit, noise, neighbourhood character) — the detail
panel renders structured data; your reply calls out what matters. Don't
stay silent after the tool completes.
</tool_protocol>

<phrase_map>
Use these as templates when translating user phrases into structured
filters for `search_apartments`:
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
                                  max_noise: "quiet"
  - "affluent neighbourhood"    → mss: {status_min: "affluent"}
  - "stable affluent area"      → mss: {status_min: "affluent",
                                        dynamics: "stable"}
  - "up-and-coming" /
    "gentrifying"               → mss: {status_min: "disadvantaged",
                                        dynamics: "improving"}
  - "near a Grundschule"        → school: {school_type: "Grundschule"}
  - "near a lake" /
    "by the water"              → near_water: "near"
</phrase_map>
"""


@toolset.instructions
def tool_protocol_instructions() -> str:
    """Toolset-scoped guidance: how to use these tools, with a phrase map.

    Pydantic AI appends this after `agent.instructions` when composing the
    system prompt — co-locating tool guidance with the tool implementations
    means renaming a tool is one atomic edit (function name + this text).
    """
    return _TOOL_PROTOCOL


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
    radius_km: float = 2.0,
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
    # Geo-context (Berlin Sozialmonitoring / transit / parks / noise / ...)
    transit: TransitFilter | None = None,
    school: SchoolFilter | None = None,
    hospital: HospitalFilter | None = None,
    mss: MssFilter | None = None,
    near_park: NearSpec | None = None,
    near_playground: NearSpec | None = None,
    near_water: NearSpec | None = None,
    max_noise: NoiseLabel | None = None,
    min_greenery: GreeneryLabel | None = None,
    density: DensityLabel | None = None,
    sort_by: SortBy = "relevance",
) -> ToolReturn:
    """Search for apartments in Berlin. Replaces the current result set.

    Berlin renters search structurally — by warm rent, rooms, district, WBS,
    move-in date, amenities. Use as many filters as the user has given you;
    leave the rest unset.

    Args:
        query: Natural language query for semantic matching. Optional —
            structured filters (below) are the primary search surface.

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
        radius_km: Search radius in km (used with near_lat/near_lon).

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

        mss: Filter by neighbourhood socioeconomic character
            (Sozialmonitoring). Fields:
              - `status_min`: minimum status floor — one of
                `"disadvantaged"`, `"lower-income"` (default), `"mixed"`,
                `"affluent"`. `"mixed"` matches mixed AND affluent areas.
              - `dynamics`: exact trend match — one of `"improving"`,
                `"stable"`, `"slipping"` (the last is counterintuitive — it
                means improving slower than the citywide trend).
            Neutral labels, not value judgements. Examples:
            "affluent neighbourhood" → `{"status_min": "affluent"}`.
            "up-and-coming" → `{"status_min": "disadvantaged",
            "dynamics": "improving"}` (the classic gentrification signature).

        near_park: Require a non-cemetery park within this distance.
            Same `NearSpec` ladder as `transit.distance` — `"next_to"` /
            `"very_near"` / `"near"` / `"walking_distance"` /
            `"bike_distance"`, or an int. Example: "park nearby" →
            `"near"`.

        near_playground: Require a playground within this distance.
            Same ladder. Example: "playground for the kids" → `"near"`.

        near_water: Require a water body (lake / river / canal) within
            this distance. Same ladder.

        max_noise: Maximum Lden noise level. `"quiet"` (< 55 dB, WHO
            health-threshold) or `"lively"` (< 65 dB, normal urban band).
            Example: "quiet street" → `"quiet"`.

        min_greenery: Minimum greenery level (WHO Europe rule: ≥0.5 ha
            green within 300m = leafy; ≥1 ha or ≥0.5 ha within 150m =
            very_leafy). `"leafy"` or `"very_leafy"`. Example: "leafy
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
        radius_km=radius_km,
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
        mss=mss,
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
    # and the total.
    markers, preview, total = await ctx.deps.search_service.search(params)

    # SessionState is the canonical in-memory snapshot. Both the LLM (via
    # build_dynamic_state_prompt) and the frontend (via the AG-UI state
    # stream) read from here. One representation, two consumers.
    ctx.deps.state.search_params = params
    ctx.deps.state.total_results = total
    ctx.deps.state.result_markers = markers
    ctx.deps.state.preview_cards = preview
    ctx.deps.state.active_id = None
    ctx.deps.state.active_listing_detail = None

    summary = LlmResultSetView(ctx.deps.state).summary(preview)
    return _return_with_state(return_value=summary, session_state=ctx.deps.state)


# TODO(post-MVP): split into a pure-query `get_listing_prose` + pure-command
# `select_listing` / `pan_map_to` pair, called in parallel by the LLM, once
# the Generative-UI pattern-3 frontend tools land. See CLAUDE.md "Deferred /
# nice-to-have" → "Parallel tool-call patterns for split commands."
@toolset.tool
async def open_listing(
    ctx: RunContext[ChatDeps],
    indices: list[int],
) -> ToolReturn | str:
    """Open a listing's detail panel AND return its full info.

    Dual purpose by design: this is both a data-fetch (returns prose so the
    LLM can reason and write a highlight) and a UI command (sets
    `active_id`, opens the right-hand detail panel, attaches the
    neighbourhood-context blob for the Neighbourhood-context UI block).

    Listings are referenced by their 1-based index in the current result
    set — the same number the user sees on each card. Indices are stable
    until the next `search_apartments` call.

    Single-index call (`indices=[k]`) opens the detail panel for listing #k
    AND attaches the neighbourhood-context blob (transit, schools, parks,
    noise, MSS, hospitals). Multi-index calls (`indices=[k, m, …]`) anchor
    UI focus to the first index but return prose for all; no geo-context
    fetch (use it for side-by-side comparison).

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

    return _return_with_state(
        return_value=rs.detail(items), session_state=ctx.deps.state
    )


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


def _return_with_state(*, return_value: str, session_state) -> ToolReturn:
    """Emit a STATE_SNAPSHOT alongside the tool's normal return value.

    Pydantic AI's AG-UI adapter yields any `BaseEvent` placed in
    `ToolReturn.metadata`. Snapshotting the full state on every mutating
    tool call is simpler than diffing — the payload is bounded
    (≤ 260 KB for 500 listings + active detail) and CopilotKit applies
    snapshots idempotently.
    """
    return ToolReturn(
        return_value=return_value,
        metadata=[
            StateSnapshotEvent(
                type=EventType.STATE_SNAPSHOT,
                snapshot=session_state.model_dump(),
            ),
        ],
    )
