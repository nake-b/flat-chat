import logging
from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic_ai import FunctionToolset, ModelRetry, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AgentToolset

from flat_chat.chat.llm_context import LlmResultSetView
from flat_chat.chat.state import ChatDeps
from flat_chat.chat.state_emission import StateEmittingToolset
from flat_chat.listings.context import MarkerChannel, TravelTimeFilter
from flat_chat.listings.types import (
    DensityLabel,
    GreeneryLabel,
    NearSpec,
    NoiseLabel,
)
from flat_chat.routing.service import RoutingError
from flat_chat.search.geo_filters import (
    HospitalFilter,
    KitaFilter,
    SchoolFilter,
    TransitFilter,
)
from flat_chat.search.schemas import SearchParams, SortBy

logger = logging.getLogger(__name__)

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
  - `locate_place(place_name=...)` — resolve a SPECIFIC named place (a
    landmark, park, lake/river, school, kita, hospital) to candidate
    references. Returns a numbered list, each with an opaque `place_ref`.
  - `open_listing(indices=[k])` — open the detail panel for listing #k AND
    attach the neighbourhood-context blob (transit, schools, kitas, parks,
    landmarks, noise, hospitals). Pass `indices=[k, m, …]` for side-by-side
    comparison prose; UI focus anchors to the first index. NEVER pass
    UUIDs, external IDs, or anything that isn't a 1-based number visible on
    the cards.
  - `get_result_page(page=N)` — browse beyond the top 5. CSV format.
    Indices in the CSV are absolute (1..N of the whole result set), not
    page-local.
  - `apply_travel_time(near_place_ref=…, mode=…, max_minutes=…)` — add a
    commute lens to the ACTIVE results: colour the map by travel time to a
    place and (with `max_minutes`) drop listings over the cutoff. Transit or
    car. Run a search first; the lens persists across later searches.
  - `show_on_map(place_refs=[…], transit_lines=[…])` — DRAW geometries on the
    map (named places via their `place_ref`, transit lines by name like "U7")
    WITHOUT filtering. For "draw the U8 so I can see these listings against it",
    "show me the Spree", "where is Tiergarten?". Pass several at once.
  - `hide_on_map(targets=[…])` — remove SPECIFIC drawn geometries by label/id
    (as shown in `<current_state>`): "hide the U8", "remove that line". Pass
    several at once. A line that's still an active search filter redraws next
    search — drop the filter instead to keep it off.
  - `clear_map_overlays()` — wipe ALL drawn geometries ("clear the map").

Drawing geometries on the map. Two ways a geometry appears:
  1. AUTOMATICALLY — a `search_apartments` call that uses `near_place_ref` or
     `transit.lines` draws that place/line as a side effect, so results are
     shown in relation to it. You don't call anything extra.
  2. ON PURPOSE — `show_on_map(...)` draws a place/line WITHOUT changing the
     results. Use it when the user wants to see something, or proactively when
     it helps orient them (e.g. they're weighing a listing and mentioned a
     river/line nearby). `<current_state>` lists what's already drawn — never
     redraw something already there or that the user has dismissed.

Named-place search — the 2-tool flow. When the user names a SPECIFIC place
("near TU Berlin", "near the Spree", "by the Brandenburger Tor", "near
Schlachtensee"):
  1. call `locate_place(place_name="…")`,
  2. pick the best candidate (ask the user only if genuinely ambiguous),
  3. call `search_apartments(near_place_ref="<that candidate's place_ref>",
     radius_km=…)`. This matches against the place's EXACT shape (a river
     line, a campus polygon), which a coordinate radius cannot.
Generic proximity ("near A park", "near A lake/kita/school") is NOT a named
place — use the category filter directly (`near_park`, `near_water`, `kita`,
`school`), no `locate_place`.

Travel time / commute. When the user cares about how far listings are from a
specific place ("near my work at TU Berlin", "≤30 min by U-Bahn from Alex",
"how's the drive to the airport?"):
  1. `locate_place(...)` to get the destination's `place_ref` (same as named
     search),
  2. `apply_travel_time(near_place_ref="…", mode="transit"|"car",
     max_minutes=…)` on the ALREADY-SEARCHED result set. It does NOT search —
     run `search_apartments` first. It recolours the map by travel time and, if
     `max_minutes` is given, drops listings over that cutoff.
Decide filter vs. annotate by the user's words: a stated limit ("under 30 min")
→ pass `max_minutes`; "I care about the commute" / "show me how far" → omit it
(colour + annotate only, nothing dropped). Default `mode` is transit; use "car"
for "drive"/"by car". The lens sticks across later searches until changed.

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
                                  kita: {distance: "near"}, max_noise: "quiet"
  - "near a kita" / "daycare
    nearby"                     → kita: {distance: "near"}
  - "near a Grundschule"        → school: {school_type: "Grundschule"}
  - "near a lake" /
    "by the water"              → near_water: "near"
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
  - "show me the U7" /
    "trace the M10"             → show_on_map(transit_lines=["U7"])
  - "draw the U8 and U9"        → show_on_map(transit_lines=["U8", "U9"])
  - "show me the Spree" /
    "where's Tiergarten?" /
    "draw the Tiergarten"       → locate_place(…) → show_on_map(place_refs=[…])
  - "hide the U8" /
    "remove that line"          → hide_on_map(targets=["U8"])
  - "get rid of the U8 and U9"  → hide_on_map(targets=["U8", "U9"])
  - "clear the map" /
    "remove all the lines" /
    "hide everything"           → clear_map_overlays()
  - "≤30 min by U-Bahn from
    TU Berlin" / "within 25 min
    transit of my work"         → locate_place(…) → apply_travel_time(
                                  near_place_ref=…, mode="transit", max_minutes=30)
  - "max 20 min drive to the
    airport"                    → locate_place(…) → apply_travel_time(
                                  near_place_ref=…, mode="car", max_minutes=20)
  - "I work at TU Berlin, show
    me the commute" / "how far
    is each from …"             → locate_place(…) → apply_travel_time(
                                  near_place_ref=…, mode="transit")  # no cutoff
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
    near_park: NearSpec | None = None,
    near_playground: NearSpec | None = None,
    near_water: NearSpec | None = None,
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
    markers, preview, total, facets = await ctx.deps.search_service.search(params)

    # SessionState is the canonical in-memory snapshot. Both the LLM (via
    # build_dynamic_state_prompt) and the frontend (via the AG-UI state
    # stream) read from here. One representation, two consumers.
    ctx.deps.state.search_params = params
    ctx.deps.state.total_results = total
    ctx.deps.state.result_markers = markers
    ctx.deps.state.preview_cards = preview
    ctx.deps.state.facets = facets
    ctx.deps.state.active_id = None
    ctx.deps.state.active_listing_detail = None

    # Draw the geometry this search is anchored to (the Spree, the U7) so the
    # user sees results IN RELATION to it. Replaces the previous search-derived
    # overlays; pinned overlays (from show_on_map) survive.
    await _rebuild_search_overlays(ctx, params)

    # Re-apply the active commute lens (if any) so a refinement keeps the travel
    # filter/heatmap instead of silently reverting to price. No-op when no lens
    # is set (just leaves markers on the default price channel).
    await _apply_travel_lens(ctx)

    # State mutation above auto-emits a STATE_SNAPSHOT via StateEmittingToolset;
    # the tool just returns prose for the LLM.
    return LlmResultSetView(ctx.deps.state).summary(ctx.deps.state.preview_cards)


@toolset.tool
async def apply_travel_time(
    ctx: RunContext[ChatDeps],
    near_place_ref: str,
    mode: Literal["transit", "car"] = "transit",
    max_minutes: int | None = None,
) -> str:
    """Add a travel-time (commute) lens to the ACTIVE result set.

    Run `search_apartments` first — this annotates / filters the listings that
    search already found; it does not search. Recolours the map pins by travel
    time to `near_place_ref` (green = near, red = far) and shows the minutes on
    each card.

    Args:
        near_place_ref: A `place_ref` from `locate_place` for the destination
            (e.g. the user's workplace / university / a landmark).
        mode: "transit" (public transport, default) or "car" (driving).
        max_minutes: If set, DROP listings further than this many minutes (a
            hard commute filter). If omitted, keep all listings and only
            colour/annotate by travel time.

    The lens persists across later `search_apartments` refinements until the
    user changes the destination/mode or it's cleared.
    """
    state = ctx.deps.state
    if not state.result_markers:
        return (
            "There's no active result set yet. Run search_apartments first, "
            "then I can add a travel-time lens to those listings."
        )

    anchor = await ctx.deps.place_service.anchor_point(near_place_ref)
    if anchor is None:
        raise ModelRetry(
            f"Could not resolve place_ref {near_place_ref!r}. Call locate_place "
            "first and pass one of the returned place_ref tokens."
        )
    label, lat, lon = anchor

    state.travel_time_filter = TravelTimeFilter(
        anchor_label=label,
        anchor_lat=lat,
        anchor_lng=lon,
        mode=mode,
        max_minutes=max_minutes,
    )

    # Draw the destination so the user sees results in relation to it. Pinned so
    # it survives subsequent searches (like an explicit show_on_map).
    overlay = await ctx.deps.place_service.overlay_geometry(
        near_place_ref, origin="pinned"
    )
    if overlay is not None:
        _upsert_overlay(state, overlay)

    try:
        await _apply_travel_lens(ctx)
    except RoutingError as exc:
        # Routing is a fallible external dependency: drop the lens and tell the
        # agent so it can offer to proceed without travel time.
        state.travel_time_filter = None
        state.marker_channel = MarkerChannel()
        logger.warning("apply_travel_time failed: %s", exc)
        return (
            f"I couldn't reach the {mode} routing service to compute travel "
            f"times to {label}. The listings are unchanged — want me to "
            "continue without the commute filter?"
        )

    how = "driving" if mode == "car" else "transit"
    if max_minutes is not None:
        return (
            f"Filtered to {state.total_results} listings within {max_minutes} "
            f"min {how} of {label}. The map is now coloured by travel time "
            "(green = closer)."
        )
    return (
        f"Coloured the map by {how} time to {label} (green = closer). "
        f"All {state.total_results} listings are still shown."
    )


async def _apply_travel_lens(ctx: RunContext[ChatDeps]) -> None:
    """Derive `channel_value` + `marker_channel` from the active travel lens.

    Shared by `search_apartments` (re-apply after a refinement) and
    `apply_travel_time` (apply on demand). Stateless w.r.t. ordering: keeps the
    search's sort order, only annotating each marker with travel minutes and —
    when `max_minutes` is set — dropping the ones over the cutoff. Filtering
    preserves order, so `preview_cards` stays a true prefix of `result_markers`.

    No lens → resets to the default `price_warm` channel (markers keep the price
    value search already wrote). Raises `RoutingError` on engine failure."""
    state = ctx.deps.state
    filt = state.travel_time_filter
    if filt is None:
        if state.marker_channel.key != "price_warm":
            state.marker_channel = MarkerChannel()
        return

    minutes_by_id = await ctx.deps.routing_service.resolve(state.result_markers, filt)

    new_markers = []
    for m in state.result_markers:
        minutes = minutes_by_id.get(m.id)
        if filt.max_minutes is not None and (
            minutes is None or minutes > filt.max_minutes
        ):
            continue  # over the cutoff (or unreachable) → drop
        new_markers.append(m.model_copy(update={"channel_value": minutes}))

    surviving = {m.id for m in new_markers}
    state.result_markers = new_markers
    state.total_results = len(new_markers)
    state.preview_cards = [c for c in state.preview_cards if c.id in surviving]
    state.marker_channel = MarkerChannel(
        key="commute_min", label=f"min to {filt.anchor_label}"
    )


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


@toolset.tool
async def show_on_map(
    ctx: RunContext[ChatDeps],
    place_refs: list[str] | None = None,
    transit_lines: list[str] | None = None,
) -> str:
    """Draw one or more places / transit lines on the map WITHOUT changing results.

    Use this when the user wants to SEE geometries in relation to the current
    listings — "draw the U8 so I can see these against it", "show me the Spree",
    "where's Tiergarten?" — or proactively when it helps orient them. It does
    NOT filter; the result set and map markers are untouched. (To filter
    listings near a place, use `search_apartments(near_place_ref=…)` instead —
    that draws the geometry too, as a side effect.)

    Draw SEVERAL at once by passing lists (`transit_lines=["U8", "U9"]`) rather
    than calling this tool repeatedly — one call draws them in a single atomic
    update. Each overlay is PINNED: it stays on the map across subsequent
    searches until removed (`hide_on_map` / `clear_map_overlays`) or the user
    dismisses it. Drawing the same place/line again just refreshes it. The
    `<current_state>` block lists what's already drawn — don't redraw something
    that's there or that the user hid.

    Args:
        place_refs: Zero or more opaque `place_ref`s from `locate_place` (named
            parks, lakes/rivers, landmarks, etc.). Resolve each name with
            `locate_place` first, then pass the chosen candidates' refs here.
        transit_lines: Zero or more line names like ["U7", "S41", "M10"]. Passed
            by name directly — no `locate_place` needed (line names are
            unambiguous).
    """
    places = place_refs or []
    lines = transit_lines or []
    if not places and not lines:
        return (
            "Pass at least one place_ref (from locate_place) or transit_line "
            '(e.g. "U7") to draw something.'
        )

    # Resolve each ref/line to a geometry and pin it; track what resolved
    # (`drawn`) vs what didn't (`missed`) so the return note can report both.
    drawn: list[str] = []
    missed: list[str] = []

    # Places: each opaque place_ref → a named_places geometry via PlaceService.
    for ref in places:
        overlay = await ctx.deps.place_service.overlay_geometry(ref, origin="pinned")
        if overlay is not None:
            _upsert_overlay(ctx.deps.state, overlay)  # replaces a same-id overlay
            drawn.append(overlay.label)
        else:
            missed.append(f'place_ref "{ref}"')

    # Transit lines: line name → route-shape geometry (display-only resolver).
    for line in lines:
        overlay = await ctx.deps.transit_overlay_service.route_geometry(
            line, origin="pinned"
        )
        if overlay is not None:
            _upsert_overlay(ctx.deps.state, overlay)
            drawn.append(overlay.label)
        else:
            missed.append(f'line "{line}"')

    if not drawn:
        return (
            f"Couldn't find {', '.join(missed)} to draw. For a place, resolve it "
            "with locate_place first; transit lines must be a real line name."
        )
    note = f"Drawing {', '.join(drawn)} on the map."
    if missed:
        note += f" (Couldn't find {', '.join(missed)}.)"
    return note


@toolset.tool
async def clear_map_overlays(ctx: RunContext[ChatDeps]) -> str:
    """Remove ALL geometries currently drawn on the map (lines, shapes, zones).

    Use when the user wants a clean map ("clear the map", "remove those lines",
    "hide everything"). Does not touch the result set or markers. To remove just
    one, the user can dismiss it directly in the UI.
    """
    overlays = ctx.deps.state.map_overlays
    if not overlays:
        return "No geometries are currently drawn on the map."
    n = len(overlays)
    ctx.deps.state.map_overlays = []
    return f"Cleared {n} geometr{'y' if n == 1 else 'ies'} from the map."


@toolset.tool
async def hide_on_map(ctx: RunContext[ChatDeps], targets: list[str]) -> str:
    """Remove SPECIFIC drawn geometries from the map (as opposed to
    `clear_map_overlays`, which wipes everything).

    Use when the user wants to hide particular overlays — "hide the U8",
    "remove that line", "get rid of the U8 and U9". Match each target against
    the overlays listed in `<current_state>` by their LABEL or id
    (case-insensitive) — e.g. pass `["U8", "Tiergarten"]`. Hide SEVERAL at once
    in one call rather than calling this tool repeatedly.

    This only edits what's drawn; it never changes the result set. A target
    that isn't currently on the map is reported back, not treated as an error.

    IMPORTANT: a line that is still an ACTIVE search filter (shown as `search`
    in `<current_state>`) will be REDRAWN on the next search. To keep such a
    line off the map for good, drop the filter instead — re-run
    `search_apartments` without it — rather than hiding it here.

    Args:
        targets: Labels or ids of overlays to remove, as shown in
            `<current_state>` (e.g. ["U8", "U9"]). Case-insensitive.
    """
    overlays = ctx.deps.state.map_overlays
    wanted = [t.strip() for t in targets if t and t.strip()]
    if not wanted:
        return "Tell me which overlay to hide (a label or id from the map)."

    # Match case-insensitively on either id or label — the agent passes whatever
    # it saw in <current_state> (usually the label, e.g. "U8").
    norm = {w.casefold() for w in wanted}
    removed = [
        o for o in overlays if o.id.casefold() in norm or o.label.casefold() in norm
    ]
    if not removed:
        return f"Nothing matching {', '.join(wanted)} is on the map right now."

    # Drop the matched overlays by id (label collisions are theoretically
    # possible; id is the stable key).
    removed_ids = {o.id for o in removed}
    ctx.deps.state.map_overlays = [o for o in overlays if o.id not in removed_ids]

    note = f"Removed {', '.join(o.label for o in removed)} from the map."

    # Report any targets that matched nothing (not an error — just feedback).
    matched = {o.id.casefold() for o in removed} | {o.label.casefold() for o in removed}
    not_found = [w for w in wanted if w.casefold() not in matched]
    if not_found:
        note += f" ({', '.join(not_found)} wasn't drawn.)"

    # A `search`-origin overlay is redrawn by the next search (it mirrors an
    # active filter), so warn that hiding alone won't keep it off the map.
    search_tied = [o.label for o in removed if o.origin == "search"]
    if search_tied:
        note += (
            f" Note: {', '.join(search_tied)} is tied to the active search and "
            "will redraw on the next search — drop that filter to keep it off."
        )
    return note


def _upsert_overlay(state, overlay) -> None:
    """Add `overlay`, replacing any existing one with the same id (stable per
    logical place/line — so redrawing refreshes rather than duplicates)."""
    state.map_overlays = [o for o in state.map_overlays if o.id != overlay.id] + [
        overlay
    ]


async def _rebuild_search_overlays(ctx: RunContext[ChatDeps], params) -> None:
    """Recompute the SEARCH-derived overlays from the active search's spatial
    anchors (`near_place_ref` / `transit.lines`), preserving pinned overlays.

    The two anchors mirror the search tool's own asymmetry: a named place goes
    through `near_place_ref`, while "near the U8" rides `transit.lines` (never
    `near_place_ref`). Both feed `map_overlays` so the geometry a search filters
    on is always the geometry drawn. A pinned overlay with the same id wins
    (it's sticky), so we never duplicate it as a search overlay."""
    kept = [o for o in ctx.deps.state.map_overlays if o.origin == "pinned"]
    kept_ids = {o.id for o in kept}
    fresh: list = []

    if params.near_place_ref:
        overlay = await ctx.deps.place_service.overlay_geometry(
            params.near_place_ref, origin="search"
        )
        if overlay is not None and overlay.id not in kept_ids:
            fresh.append(overlay)

    if params.transit is not None and params.transit.lines:
        for line in params.transit.lines:
            overlay = await ctx.deps.transit_overlay_service.route_geometry(
                line, origin="search"
            )
            if overlay is not None and overlay.id not in kept_ids:
                fresh.append(overlay)

    ctx.deps.state.map_overlays = kept + fresh


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
class ListingsCapability(AbstractCapability[ChatDeps]):
    """The apartment search + listing tools bundled as a v2 capability.

    The tools (`search_apartments`, `open_listing`, `get_result_page`,
    `locate_place`, `show_on_map`, `clear_map_overlays`) and their
    `@toolset.instructions` protocol guidance are unchanged — this composes the
    agent via `capabilities=[...]` (Pydantic AI v2's primary extension
    primitive) instead of a bare `toolsets=[...]`. `get_toolset` is called once
    at Agent construction, so the toolset's tools are all registered by then.

    It returns the toolset wrapped in `StateEmittingToolset` so any `deps.state`
    mutation a tool makes auto-emits a STATE_SNAPSHOT to the frontend — emission
    stays structural (the wrapper intercepts `call_tool`), not something each
    tool body has to remember. The old `_return_with_state` helper is gone for
    the same reason. See `state_emission.py` and `map-overlays.md`.

    New agent-callable tool groups (e.g. the map/frontend command tools and
    distance tools) should land as their OWN capabilities — optionally with
    `defer_loading=True` to keep them out of the cached prompt prefix until the
    model loads them. See agent-compound-docs/decisions/pydantic-v2-migration.md.
    """

    def get_toolset(self) -> AgentToolset[ChatDeps] | None:
        return StateEmittingToolset(toolset)
