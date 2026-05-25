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
    price_warm_max: float | None = None,
    rooms_min: float | None = None,
    rooms_max: float | None = None,
    area_sqm_min: float | None = None,
    districts: list[str] | None = None,
    floor_min: int | None = None,
    listing_type: str | None = None,
    has_images: bool | None = None,
    near_lat: float | None = None,
    near_lon: float | None = None,
    radius_km: float = 2.0,
    sort_by: SortBy = "relevance",
) -> ToolReturn:
    """Search for apartments in Berlin. Replaces the current result set.

    Args:
        query: Natural language query for semantic matching. Optional.
        price_warm_max: Maximum warm rent in euros.
        rooms_min: Minimum number of rooms.
        rooms_max: Maximum number of rooms.
        area_sqm_min: Minimum area in square meters.
        districts: Berlin district or neighborhood names to restrict to.
            Substring match — both Bezirke ("Mitte", "Friedrichshain-Kreuzberg",
            "Pankow", "Charlottenburg-Wilmersdorf", "Spandau", "Steglitz-Zehlendorf",
            "Tempelhof-Schöneberg", "Neukölln", "Treptow-Köpenick",
            "Marzahn-Hellersdorf", "Lichtenberg", "Reinickendorf") and Ortsteile
            ("Kreuzberg", "Prenzlauer Berg", "Wedding", "Schöneberg", ...) work.
        floor_min: Minimum floor number.
        listing_type: Optional raw listing-type filter (data is not yet
            normalized — values vary by source, e.g. "Etagenwohnung",
            "1 Room Flat"). Leave unset unless the user explicitly names one.
        has_images: If true, exclude listings without images. Default (None)
            returns all listings — leave unset unless the user explicitly asks
            for photos only.
        near_lat: Latitude for proximity search.
        near_lon: Longitude for proximity search.
        radius_km: Search radius in km (used with near_lat/near_lon).
        sort_by: "relevance" (requires query — otherwise falls back to recent),
            "price", "area", or "recent".
    """
    params = SearchParams(
        query=query,
        price_warm_max=price_warm_max,
        rooms_min=rooms_min,
        rooms_max=rooms_max,
        area_sqm_min=area_sqm_min,
        districts=districts,
        floor_min=floor_min,
        listing_type=listing_type,
        has_images=has_images,
        near_lat=near_lat,
        near_lon=near_lon,
        radius_km=radius_km,
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
