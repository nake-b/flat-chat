"""FastAPI dependency wiring.

Single seam between the framework (FastAPI request scope) and the domain
services. Other layers should never instantiate services directly — they
go through these `Depends(...)` factories so the request scope owns the
session lifecycle.
"""

from fastapi import Depends
from pydantic_ai import Embedder
from sqlalchemy.ext.asyncio import AsyncSession

from flat_chat.chat.sessions import InMemorySessionStore, SessionStore
from flat_chat.core.database import get_async_db
from flat_chat.core.embedder import get_embedder
from flat_chat.listings.service import ListingService
from flat_chat.search.service import SearchService

# Process-lifetime singleton — survives across requests, dies with the worker.
# Swap for a Postgres-backed store when persistence lands.
_session_store: SessionStore = InMemorySessionStore()


def get_session_store() -> SessionStore:
    return _session_store


def get_listing_service(
    db: AsyncSession = Depends(get_async_db),
) -> ListingService:
    return ListingService(db)


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
    from flat_chat.chat.example_backend import ExampleSearchBackend
    from flat_chat.chat.service import ChatService

    # 👉 HACKATHON: swap ExampleSearchBackend for your own AgentBackend here.
    #    Everything else in the request path stays as-is. See HACKATHON.md.
    return ChatService(
        search_service=search_service,
        listing_service=listing_service,
        store=store,
        backend=ExampleSearchBackend(),
    )
