from pydantic_ai import FunctionToolset, RunContext

from flat_chat.chat.state import ChatDeps, ResultSet
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
) -> str:
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
    ctx.deps.session.result_set = ResultSet(df=df, params=params, notes=notes)
    return ctx.deps.session.result_set.summary()


@toolset.tool
async def get_result_details(
    ctx: RunContext[ChatDeps],
    indices: list[int],
) -> str:
    """Show full details for specific listings from the current result set.

    Args:
        indices: 1-based positions referring to the most recent search/page output.
    """
    rs = ctx.deps.session.result_set
    if rs is None:
        return "No active search results. Run search_apartments first."
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
