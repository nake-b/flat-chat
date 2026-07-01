"""Bookmarks — per-user saved listings.

HTTP-only (no agent involvement; see agent-vs-http-data-flow.md). Idempotent
add + remove so the frontend can fire optimistically without seeing 404/409
noise on double-clicks.

  POST   /api/bookmarks/{listing_id}   200   add (idempotent)
  DELETE /api/bookmarks/{listing_id}   204   remove (idempotent)
  GET    /api/bookmarks/ids            200   list[str]   — fast star-hydrate
  GET    /api/bookmarks                200   ListingCard[] — sidebar + map
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response, status

from flat_chat.core.dependencies import (
    get_bookmark_service,
    get_user_id,
    valid_listing_id,
)
from flat_chat.listings.bookmarks import BookmarkService
from flat_chat.listings.context import ListingCard

router = APIRouter()


# Static paths must precede the parametric `/{listing_id}` ones — FastAPI matches
# in declaration order, and a bare `GET /ids` would otherwise collide with a
# hypothetical `GET /{listing_id}` reader (we don't ship one today, but ordering
# stays correct in case we add it later).
@router.get("/ids", response_model=list[str])
async def list_bookmark_ids(
    user_id: str = Depends(get_user_id),
    service: BookmarkService = Depends(get_bookmark_service),
) -> list[str]:
    """Just the ids — fast mount-time hydration of star-state on every card.

    Newest-first by `created_at`, but the frontend only cares about set
    membership; order is informational.
    """
    return await service.list_ids(user_id)


@router.get("", response_model=list[ListingCard])
async def list_bookmarks(
    user_id: str = Depends(get_user_id),
    service: BookmarkService = Depends(get_bookmark_service),
) -> list[ListingCard]:
    """Hydrated tier-2 cards for the bookmark sidebar (and bookmark-mode map
    pins). Newest first."""
    return await service.list_cards(user_id)


@router.post("/{listing_id}", status_code=status.HTTP_200_OK)
async def add_bookmark(
    listing_id: uuid.UUID = Depends(valid_listing_id),
    user_id: str = Depends(get_user_id),
    service: BookmarkService = Depends(get_bookmark_service),
) -> dict[str, str]:
    """Bookmark a listing. Idempotent — re-adding is 200, not 409."""
    await service.add(user_id, listing_id)
    return {"status": "ok"}


@router.delete("/{listing_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_bookmark(
    listing_id: uuid.UUID = Depends(valid_listing_id),
    user_id: str = Depends(get_user_id),
    service: BookmarkService = Depends(get_bookmark_service),
) -> Response:
    """Remove a bookmark. 204 either way — idempotent for optimistic UI."""
    await service.remove(user_id, listing_id)
    return Response(status_code=204)
