"""Direct HTTP reads for listing data — agent-bypass path.

Two reads, by representation tier (an AIP-157 `view` enum, not a path):
  - `GET /api/listings/{id}` → tier-3 `ListingDetail` for one listing.
  - `GET /api/listings?ids=a&ids=b&…&view=card` → tier-2 `ListingCard`s for a
    batch, in request order. The card strip's lazy-hydrate path (markers
    scrolling into view); future bookmarks hydrate saved ids the same way.

Both are idempotent + browser-cacheable. Search stays agent-only (the LLM
owns query interpretation); listing *reads* are everyone's business — the
same `ListingService` powers these routes and the agent's tools.

Architecture-decision doc: `agent-compound-docs/decisions/agent-vs-http-data-flow.md`
"""

import uuid
from enum import StrEnum

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from flat_chat.core.dependencies import get_listing_service, valid_listing_id
from flat_chat.listings.context import ListingCard, ListingDetail
from flat_chat.listings.service import ListingService

router = APIRouter()

# A viewport's worth of cards per request. Bounds the query-string length too
# (100 × ~37-char uuids ≈ 3.7k, under nginx's 8k header buffer).
_MAX_BATCH_IDS = 100


class ListingView(StrEnum):
    """Representation tier selected by `?view=` (AIP-157 view enum)."""

    card = "card"  # tier-2 — the card-strip shape
    detail = "detail"  # tier-3 — the full detail panel (single-item route only)


@router.get("", response_model=list[ListingCard])
async def list_listings(
    response: Response,
    ids: list[str] = Query(default=[]),
    view: ListingView = ListingView.card,
    service: ListingService = Depends(get_listing_service),
) -> list[ListingCard]:
    """Batch-read listings by id, in the requested order.

    The lazy-hydrate path for the card strip. `view` selects the
    representation; only `card` (tier-2) is implemented on the collection —
    tier-3 detail is the single-item route `GET /api/listings/{id}`. Returns
    cards in the same order as `ids`; ids with no matching listing are
    omitted. Cacheable (`view` is in the query string, so cache keys per
    `(ids, view)`).
    """
    if view is not ListingView.card:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="only view=card is supported on the collection; "
            "use GET /api/listings/{id} for tier-3 detail",
        )
    if len(ids) > _MAX_BATCH_IDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"too many ids (max {_MAX_BATCH_IDS} per request)",
        )
    response.headers["Cache-Control"] = "public, max-age=300"
    if not ids:
        return []
    return await service.get_cards(ids)


@router.get("/{listing_id}", response_model=ListingDetail)
async def get_listing(
    response: Response,
    listing_id: uuid.UUID = Depends(valid_listing_id),
    service: ListingService = Depends(get_listing_service),
) -> ListingDetail:
    """Return one listing's full tier-3 detail.

    Cache-Control: 5 minutes — gold rebuilds on the daily listings ETL
    cadence, so a 5-minute browser cache covers re-clicks within a
    single user session without serving stale data across days.
    """
    detail = await service.get_detail(listing_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="listing not found"
        )
    response.headers["Cache-Control"] = "public, max-age=300"
    return detail
