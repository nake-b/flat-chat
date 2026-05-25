from ag_ui.core import EventType, StateSnapshotEvent
from pydantic_ai import FunctionToolset, RunContext, ToolReturn

from flat_chat.chat.state import ChatDeps, ResultSet
from flat_chat.chat.ui_state import UiApartment
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
    ctx.deps.state.results = [UiApartment.from_dataframe_row(row) for _, row in df.iterrows()]
    ctx.deps.state.active_id = None

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

    Args:
        indices: 1-based positions referring to the most recent search/page output.
    """
    rs = ctx.deps.session.result_set
    if rs is None:
        return "No active search results. Run search_apartments first."

    # Single-index calls are a UI "expand this card" hint — open the detail
    # panel for that listing. Multi-index calls snap active_id to the first
    # index in the batch so the UI has a consistent anchor (the first card
    # the user asked about).
    first = indices[0]
    pos = first - 1
    if 0 <= pos < len(ctx.deps.state.results):
        ctx.deps.state.active_id = ctx.deps.state.results[pos].id

    return _return_with_state(return_value=rs.detail(indices), ui_state=ctx.deps.state)


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
