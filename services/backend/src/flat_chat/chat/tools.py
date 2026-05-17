from __future__ import annotations

from pydantic_ai import RunContext

from flat_chat.chat.agent import ChatDeps, ResultSet, agent
from flat_chat.search.schemas import SearchFilters


@agent.tool
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
    sort_by: str = "relevance",
    limit: int = 10,
) -> str:
    """Search for apartments in Berlin.

    Args:
        query: Natural language search query for semantic matching.
        price_warm_max: Maximum warm rent in euros.
        rooms_min: Minimum number of rooms.
        rooms_max: Maximum number of rooms.
        area_sqm_min: Minimum area in square meters.
        districts: Berlin districts to search in.
        floor_min: Minimum floor number.
        listing_type: Type of listing (e.g. "WG", "Wohnung").
        has_images: Only return listings with images.
        near_lat: Latitude for proximity search.
        near_lon: Longitude for proximity search.
        radius_km: Search radius in km (default 2.0, used with near_lat/near_lon).
        sort_by: Sort order — "relevance", "price", or "area".
        limit: Maximum number of results (default 10).
    """
    filters = SearchFilters(
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
        limit=limit,
    )

    df = await ctx.deps.search_service.search(filters)
    ctx.deps.result_set = ResultSet(df=df, filters=filters, total=len(df))

    if df.empty:
        return (
            "No apartments found matching those criteria. "
            "Try broadening your search."
        )

    price_range = ""
    prices = df["price_warm_eur"].dropna()
    if not prices.empty:
        price_range = f" Price range: €{prices.min():.0f}–€{prices.max():.0f}."

    districts_found = df["district"].dropna().unique().tolist()
    district_str = ", ".join(districts_found[:5]) if districts_found else "various"

    top_listings = []
    for i, row in df.head(5).iterrows():
        parts = []
        if row.get("title"):
            parts.append(row["title"])
        if row.get("price_warm_eur"):
            parts.append(f"€{row['price_warm_eur']:.0f}")
        if row.get("rooms"):
            parts.append(f"{row['rooms']}rm")
        if row.get("district"):
            parts.append(row["district"])
        top_listings.append(f"  {int(i) + 1}. {' | '.join(parts)}")

    summary = (
        f"Found {len(df)} apartments.{price_range} "
        f"Districts: {district_str}.\n"
        f"Top results:\n" + "\n".join(top_listings)
    )
    return summary


@agent.tool
async def get_result_details(
    ctx: RunContext[ChatDeps],
    indices: list[int],
) -> str:
    """Show details for specific listings from current results.

    Args:
        indices: List of result positions (1-based) to show details for.
    """
    if not ctx.deps.result_set:
        return "No active search results. Run a search first."

    df = ctx.deps.result_set.df
    details = []
    for idx in indices:
        pos = idx - 1
        if pos < 0 or pos >= len(df):
            details.append(f"#{idx}: Invalid index (results are 1–{len(df)})")
            continue

        row = df.iloc[pos]
        lines = [f"--- Listing #{idx} ---"]
        if row.get("title"):
            lines.append(f"Title: {row['title']}")
        if row.get("price_warm_eur"):
            lines.append(f"Warm rent: €{row['price_warm_eur']:.0f}/month")
        if row.get("price_cold_eur"):
            lines.append(f"Cold rent: €{row['price_cold_eur']:.0f}/month")
        if row.get("rooms"):
            lines.append(f"Rooms: {row['rooms']}")
        if row.get("area_sqm"):
            lines.append(f"Area: {row['area_sqm']:.0f} m²")
        if row.get("floor") is not None:
            lines.append(f"Floor: {row['floor']}")
        if row.get("district"):
            lines.append(f"District: {row['district']}")
        if row.get("address"):
            lines.append(f"Address: {row['address']}")
        if row.get("available_from"):
            lines.append(f"Available from: {row['available_from']}")
        if row.get("listing_type"):
            lines.append(f"Type: {row['listing_type']}")
        if row.get("source_url"):
            lines.append(f"URL: {row['source_url']}")
        details.append("\n".join(lines))

    return "\n\n".join(details)


@agent.tool
async def get_result_page(
    ctx: RunContext[ChatDeps],
    page: int = 1,
    page_size: int = 5,
) -> str:
    """Show a page of results from the current search.

    Args:
        page: Page number (1-based).
        page_size: Number of results per page (default 5).
    """
    if not ctx.deps.result_set:
        return "No active search results. Run a search first."

    df = ctx.deps.result_set.df
    total = len(df)
    start = (page - 1) * page_size
    end = min(start + page_size, total)

    if start >= total:
        total_pages = (total - 1) // page_size + 1
        return (
            f"Page {page} is out of range. "
            f"There are {total} results ({total_pages} pages)."
        )

    page_df = df.iloc[start:end]
    lines = [f"Results {start + 1}–{end} of {total}:"]
    for i, (_, row) in enumerate(page_df.iterrows()):
        num = start + i + 1
        parts = []
        if row.get("title"):
            parts.append(row["title"])
        if row.get("price_warm_eur"):
            parts.append(f"€{row['price_warm_eur']:.0f}")
        if row.get("rooms"):
            parts.append(f"{row['rooms']}rm")
        if row.get("area_sqm"):
            parts.append(f"{row['area_sqm']:.0f}m²")
        if row.get("district"):
            parts.append(row["district"])
        lines.append(f"  {num}. {' | '.join(parts)}")

    return "\n".join(lines)
