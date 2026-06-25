"""Direct HTTP reads for listing data — agent-bypass path.

`GET /api/listings/{id}` returns the full tier-3 detail blob for one
listing, with a 5-minute browser cache. Used by:
  - The frontend's detail panel when the user clicks a card (the primary
    consumer — agent doesn't have to be involved for direct viewing)
  - Future shareable URLs (`/listing/<uuid>` deep links)
  - Future bookmarks list rendering (hydrates tier-2 cards for saved IDs)

Search remains agent-only (the LLM owns query interpretation); listing
*reads* are everyone's business. Same `ListingService.get(id)` powers
both this route and the agent's `open_listing` tool — single accessor,
multiple callers.

Architecture-decision doc: `agent-compound-docs/decisions/agent-vs-http-data-flow.md`
"""

from fastapi import APIRouter, Depends, HTTPException, Response, status

from flat_chat.core.dependencies import get_listing_service
from flat_chat.listings.context import ListingDetail
from flat_chat.listings.service import ListingService

router = APIRouter()


@router.get("/{listing_id}", response_model=ListingDetail)
async def get_listing(
    listing_id: str,
    response: Response,
    service: ListingService = Depends(get_listing_service),
) -> ListingDetail:
    """Return one listing's full tier-3 detail.

    Cache-Control: 5 minutes — gold rebuilds on the daily listings ETL
    cadence, so a 5-minute browser cache covers re-clicks within a
    single user session without serving stale data across days.
    """
    detail = await service.get(listing_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="listing not found"
        )
    response.headers["Cache-Control"] = "public, max-age=300"
    return detail
