from ag_ui.core import EventType, StateSnapshotEvent
from pydantic_ai import FunctionToolset, RunContext, ToolReturn

from flat_chat.chat.state import ChatDeps, ResultSet
from flat_chat.chat.ui_state import UiApartment
from flat_chat.search.schemas import SearchParams, SortBy

toolset: FunctionToolset[ChatDeps] = FunctionToolset()

# Cap on tool_logs length so the AG-UI state payload doesn't grow unbounded
# across a long conversation. Lifecycle pills are ephemeral by nature.
_TOOL_LOG_KEEP = 20


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
    # all four counts (chat prose, tool_logs pill, cards, map) agree — the map
    # can only render markers for apartments with lat/lng, and silently dropping
    # them downstream caused user-visible count mismatches. The filter belongs
    # at the tool layer; SearchService stays general.
    total_before = len(df)
    df = df.dropna(subset=["latitude", "longitude"])
    df = df.reset_index(drop=True)
    skipped = total_before - len(df)

    ctx.deps.session.result_set = ResultSet(df=df, params=params, notes=notes)

    # Reset tool_logs at the start of each search — a fresh search starts a
    # new lifecycle, so stale pills from prior searches ("searched: 50") would
    # otherwise accumulate next to the current one. Follow-up tools
    # (get_result_page, get_result_details) operate on the same result set and
    # should keep appending.
    ctx.deps.state.tool_logs = []

    # Mirror into the frontend-facing UiState. The agent never reads from
    # ctx.deps.state — it's exclusively for the UI to render the map + cards.
    ctx.deps.state.results = [UiApartment.from_dataframe_row(row) for _, row in df.iterrows()]
    ctx.deps.state.active_id = None
    # Single, clean status line. Coord-less listings are silently dropped
    # (they're not renderable on the map anyway) — surfacing a separate
    # "K skipped" warning in the chat indicator was noise.
    _push_log(ctx.deps.state.tool_logs, _format_search_summary(params, len(df)))

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

    # If the user is asking about exactly one listing, treat that as a UI
    # "expand this card" hint so the frontend's detail panel opens
    # automatically. Multi-listing detail calls leave active_id alone.
    state_mutated = False
    if len(indices) == 1:
        pos = indices[0] - 1
        if 0 <= pos < len(ctx.deps.state.results):
            ctx.deps.state.active_id = ctx.deps.state.results[pos].id
            _push_log(ctx.deps.state.tool_logs, f"Opened listing #{indices[0]}.")
            state_mutated = True

    if state_mutated:
        return _return_with_state(return_value=rs.detail(indices), ui_state=ctx.deps.state)
    return rs.detail(indices)


@toolset.tool
async def get_result_page(
    ctx: RunContext[ChatDeps],
    page: int = 1,
    page_size: int = 10,
) -> str:
    """Show a compact page of the current result set.

    Args:
        page: 1-based page number.
        page_size: Listings per page (default 10).
    """
    rs = ctx.deps.session.result_set
    if rs is None:
        return "No active search results. Run search_apartments first."
    return rs.page(page, page_size)


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


def _push_log(logs: list[str], entry: str) -> None:
    logs.append(entry)
    if len(logs) > _TOOL_LOG_KEEP:
        del logs[: len(logs) - _TOOL_LOG_KEEP]


def _format_search_summary(params: SearchParams, count: int) -> str:
    """One-line static summary of a search for the UI status indicator.

    Intentionally minimal: count + district (if filtered). Other filters
    (price, rooms, area) are omitted to keep the pill readable when many
    constraints stack — the user's chat message already shows what they
    asked for; this line confirms what was found. Future tools follow the
    same shape: short factual one-liner, tool-authored, no LLM round-trip.
    """
    base = f"Found {count} apartment{'s' if count != 1 else ''}"
    if params.districts:
        return f"{base} in {', '.join(params.districts)}."
    return f"{base}."
