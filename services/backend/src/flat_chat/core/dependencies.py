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
from flat_chat.search.service import SearchService
from flat_chat.users.models import DUMMY_USER_ID

# Process-lifetime singleton — survives across requests, dies with the worker.
# Owns its own DB sessions (via AsyncSessionLocal), independent of the request
# scope, because it persists from `on_complete` at the END of the SSE stream.
_session_store: SessionStore = DbSessionStore(AsyncSessionLocal)


def get_session_store() -> SessionStore:
    return _session_store


def get_user_id() -> str:
    """Stage-1 identity seam — returns a fixed dummy user id (no auth yet).

    The dummy user row is upserted on demand in `DbSessionStore.create`. Stage 2
    (anonymous per-browser cookie) and stage 3 (real auth → JWT `sub`) replace ONLY
    this function — every route depends on `Depends(get_user_id)`, so call sites
    never change. See session-persistence.md.
    """
    return DUMMY_USER_ID


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


def get_chat_service(
    search_service: SearchService = Depends(get_search_service),
    listing_service: ListingService = Depends(get_listing_service),
    store: SessionStore = Depends(get_session_store),
):
    # Import here to break the import cycle: chat/service.py imports
    # ChatDeps from chat/state.py which imports from listings/.
    from flat_chat.chat.service import ChatService

    return ChatService(
        search_service=search_service,
        listing_service=listing_service,
        store=store,
    )
