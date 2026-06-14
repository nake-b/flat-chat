from ag_ui.core import EventType, StateSnapshotEvent
from pydantic_ai import FunctionToolset, RunContext, ToolReturn

from flat_chat.chat.state import ChatDeps, ResultSet
from flat_chat.chat.ui_state import UiApartment
from flat_chat.search.buckets import DensityLabel, GreeneryLabel, NoiseLabel
from flat_chat.search.geo_filters import (
    HospitalFilter,
    MssFilter,
    NearSpec,
    SchoolFilter,
    TransitFilter,
)
from flat_chat.search.schemas import SearchParams, SortBy

toolset: FunctionToolset[ChatDeps] = FunctionToolset()


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
    available_by: str | None = None,
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
        available_by: Latest acceptable move-in date as ISO `YYYY-MM-DD`.
            Matches listings whose `available_from` is on or before this date.

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

    # Surface soft-fallback signals to the LLM via ResultSet.notes — these
    # also flow back to the user. The service silently degrades, so this is
    # the user-facing voice for those degradations.
    notes: list[str] = []
    if params.query and ctx.deps.search_service.embedder is None:
        notes.append(
            "Semantic ranking unavailable (no embedder configured) — results "
            "filtered by metadata only and sorted by recency instead of relevance."
        )
    elif params.sort_by == "relevance" and not params.query:
        notes.append(
            "Sort=relevance requires a query — results sorted by recency instead."
        )

    df = await ctx.deps.search_service.search(params)

    # Drop listings without coordinates BEFORE building ResultSet + UiState so
    # all three counts (chat prose, cards, map) agree — the map can only render
    # markers for apartments with lat/lng, and silently dropping them downstream
    # caused user-visible count mismatches. The filter belongs at the tool layer;
    # SearchService stays general.
    df = df.dropna(subset=["latitude", "longitude"])
    df = df.reset_index(drop=True)

    ctx.deps.session.result_set = ResultSet(df=df, params=params, notes=notes)

    # Mirror into the frontend-facing UiState. The agent never reads from
    # ctx.deps.state — it's exclusively for the UI to render the map + cards.
    # Status-pill copy is owned entirely by the frontend (state/toolStatus.ts);
    # tools push data only.
    ctx.deps.state.results = [
        UiApartment.from_dataframe_row(row) for _, row in df.iterrows()
    ]
    ctx.deps.state.active_id = None
    ctx.deps.state.active_listing_context = None

    return _return_with_state(
        return_value=ctx.deps.session.result_set.summary(),
        ui_state=ctx.deps.state,
    )


@toolset.tool
async def get_result_details(
    ctx: RunContext[ChatDeps],
    indices: list[int],
) -> ToolReturn | str:
    """Show full details for specific listings from the current result set.

    This is the SINGLE detail entrypoint. Listings are referenced by their
    1-based index in the current result set — the same number the user sees
    on each card. Indices are stable until the next `search_apartments` call.

    Single-index call (`indices=[k]`) opens the detail panel for listing #k
    AND attaches the neighbourhood-context blob (transit, schools, parks,
    noise, MSS, hospitals). Multi-index calls (`indices=[k, m, …]`) just
    return prose for comparison; no context fetch.

    Args:
        indices: 1-based positions referring to the most recent search/page
            output. NEVER pass UUIDs, external IDs, or anything that isn't a
            simple 1-based number visible to the user.
    """
    rs = ctx.deps.session.result_set
    if rs is None:
        return "No active search results. Run search_apartments first."

    # Anchor the detail panel to indices[0] regardless of count, so the UI
    # has a consistent "the first card the user asked about" anchor.
    first = indices[0]
    pos = first - 1
    geo_prose = ""
    if 0 <= pos < len(ctx.deps.state.results):
        active = ctx.deps.state.results[pos]
        ctx.deps.state.active_id = active.id
        # Single-index calls fetch the geo-context blob and surface it both
        # in UiState (so the frontend Neighbourhood panel renders) AND in
        # the tool's return value (so the LLM can answer follow-up questions
        # about transit / schools / noise / MSS without re-fetching).
        if len(indices) == 1:
            detail = ctx.deps.search_service.get_listing_details(active.id)
            if detail is not None:
                ctx.deps.state.active_listing_context = detail.context
                geo_prose = "\n\n" + _format_geo_context_prose(
                    first, detail.context
                )

    return _return_with_state(
        return_value=rs.detail(indices) + geo_prose, ui_state=ctx.deps.state
    )


@toolset.tool
async def get_result_page(
    ctx: RunContext[ChatDeps],
    page: int = 1,
    page_size: int = 10,
) -> ToolReturn | str:
    """Show a compact page of the current result set.

    Args:
        page: 1-based page number.
        page_size: Listings per page (default 10).
    """
    rs = ctx.deps.session.result_set
    if rs is None:
        return "No active search results. Run search_apartments first."

    return _return_with_state(
        return_value=rs.page(page, page_size), ui_state=ctx.deps.state
    )


def _format_geo_context_prose(idx: int, context) -> str:
    """LLM-facing neighbourhood-context prose for one listing.

    Appended to the standard `rs.detail([idx])` output by `get_result_details`
    when called with a single index. Mirrors what the frontend renders in the
    Neighbourhood-context detail-panel block, but as text the LLM can quote
    when the user asks follow-up questions about transit / schools / noise /
    MSS. Sections only render when they have data — partial backend wiring
    produces partial prose, never empty headings.
    """
    parts: list[str] = [f"--- Listing #{idx} — neighbourhood context ---"]

    if context.transit:
        parts.append("Nearby transit:")
        for stop in context.transit:
            lines = ", ".join(stop.lines) if stop.lines else "—"
            parts.append(
                f"  - {stop.name} — {lines} "
                f"({stop.distance_m}m, {stop.walk_minutes}min walk)"
            )

    if context.school_catchment is not None:
        sc = context.school_catchment
        parts.append(
            f"Primary school catchment: {sc.school_name or sc.catchment_id}"
        )

    if context.nearest_schools:
        parts.append("Nearby schools:")
        for s in context.nearest_schools:
            parts.append(
                f"  - {s.name or 'unnamed'} "
                f"({s.school_type or 'unknown type'}) — {s.distance_m}m"
            )

    if context.nearest_parks:
        parts.append("Nearby parks:")
        for p in context.nearest_parks:
            parts.append(f"  - {p.name or 'unnamed'} — {p.distance_m}m")

    if context.nearest_playground is not None:
        pg = context.nearest_playground
        parts.append(
            f"Nearest playground: {pg.name or 'unnamed'} — {pg.distance_m}m"
        )

    if context.nearest_hospitals:
        parts.append("Hospitals nearby:")
        for h in context.nearest_hospitals:
            parts.append(
                f"  - {h.name or 'unnamed'} ({h.tier}) — {h.distance_m}m"
            )

    if context.nearest_water is not None:
        w = context.nearest_water
        parts.append(
            f"Nearest water: {w.name or w.water_kind or 'water'} — {w.distance_m}m"
        )

    character_bits: list[str] = []
    if context.noise is not None and context.noise.label is not None:
        character_bits.append(f"street noise: {context.noise.label}")
    if context.greenery is not None and context.greenery.label is not None:
        character_bits.append(f"greenery: {context.greenery.label}")
    if context.density is not None and context.density.label is not None:
        character_bits.append(f"density: {context.density.label}")
    if context.mss is not None and context.mss.status_label is not None:
        mss_bits = [context.mss.status_label]
        if context.mss.dynamics_label is not None:
            mss_bits.append(context.mss.dynamics_label)
        character_bits.append(
            f"Sozialmonitoring: {' · '.join(mss_bits)}"
        )
    if character_bits:
        parts.append("Neighbourhood character: " + ", ".join(character_bits))

    if context.disabled_parking_count > 0:
        count = context.disabled_parking_count
        parts.append(f"Disabled parking nearby: {count} spots within 300m")
    return "\n".join(parts)


def _return_with_state(*, return_value: str, ui_state) -> ToolReturn:
    """Emit a STATE_SNAPSHOT alongside the tool's normal return value.

    Pydantic AI's AG-UI adapter yields any `BaseEvent` placed in
    `ToolReturn.metadata`. Snapshotting the full state on every mutating
    tool call is simpler than diffing — the payload is small (≤ a few KB)
    and CopilotKit applies snapshots idempotently.
    """
    return ToolReturn(
        return_value=return_value,
        metadata=[
            StateSnapshotEvent(
                type=EventType.STATE_SNAPSHOT,
                snapshot=ui_state.model_dump(),
            ),
        ],
    )
