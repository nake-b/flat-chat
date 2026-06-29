"""FastAPI dependency wiring.

Single seam between the framework (FastAPI request scope) and the domain
services. Other layers should never instantiate services directly — they
go through these `Depends(...)` factories so the request scope owns the
session lifecycle.
"""

from fastapi import Depends
from pydantic_ai import Embedder
from sqlalchemy.ext.asyncio import AsyncSession

from flat_chat.chat.sessions import DbSessionStore, SessionStore
from flat_chat.core.database import AsyncSessionLocal, get_async_db
from flat_chat.core.embedder import get_embedder
from flat_chat.listings.bookmarks_service import BookmarkService
from flat_chat.listings.service import ListingService
from flat_chat.search.places import PlaceService
from flat_chat.search.service import SearchService
from flat_chat.search.transit_overlays import TransitOverlayService
from flat_chat.users.auth import current_active_user
from flat_chat.users.models import User

# Process-lifetime singleton — survives across requests, dies with the worker.
# Owns its own DB sessions (via AsyncSessionLocal), independent of the request
# scope, because it persists from `on_complete` at the END of the SSE stream.
_session_store: SessionStore = DbSessionStore(AsyncSessionLocal)


def get_session_store() -> SessionStore:
    return _session_store


async def get_user_id(user: User = Depends(current_active_user)) -> str:
    """The identity seam — resolves the authenticated user id from the cookie.

    Every route that needs identity depends on `Depends(get_user_id)`; this is the
    ONE place auth is wired in, so call sites never change. Resolves the
    fastapi-users `current_active_user` (401 when there's no valid session cookie)
    and returns its id as a string — the shape the storage layer + ownership
    checks expect. See AUTH.md.

    Tests override this dependency directly (`app.dependency_overrides[get_user_id]`)
    to run as an arbitrary user without minting a real cookie.
    """
    return str(user.id)


def get_listing_service(
    db: AsyncSession = Depends(get_async_db),
) -> ListingService:
    return ListingService(db)


def get_bookmark_service(
    db: AsyncSession = Depends(get_async_db),
    listing_service: ListingService = Depends(get_listing_service),
) -> BookmarkService:
    return BookmarkService(db, listing_service)


def get_search_service(
    db: AsyncSession = Depends(get_async_db),
    embedder: Embedder | None = Depends(get_embedder),
) -> SearchService:
    return SearchService(db, embedder)


def get_place_service(
    db: AsyncSession = Depends(get_async_db),
) -> PlaceService:
    return PlaceService(db)


def get_transit_overlay_service(
    db: AsyncSession = Depends(get_async_db),
) -> TransitOverlayService:
    return TransitOverlayService(db)


def get_chat_service(
    search_service: SearchService = Depends(get_search_service),
    listing_service: ListingService = Depends(get_listing_service),
    place_service: PlaceService = Depends(get_place_service),
    transit_overlay_service: TransitOverlayService = Depends(
        get_transit_overlay_service
    ),
    store: SessionStore = Depends(get_session_store),
):
    # Import here to break the import cycle: chat/service.py imports
    # ChatDeps from chat/state.py which imports from listings/.
    from flat_chat.chat.service import ChatService

    return ChatService(
        search_service=search_service,
        listing_service=listing_service,
        place_service=place_service,
        transit_overlay_service=transit_overlay_service,
        store=store,
    )
