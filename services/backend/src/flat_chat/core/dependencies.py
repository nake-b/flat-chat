from fastapi import Depends
from pydantic_ai import Embedder
from sqlalchemy.orm import Session

from flat_chat.chat.sessions import InMemorySessionStore, SessionStore
from flat_chat.core.database import get_db
from flat_chat.core.embedder import get_embedder
from flat_chat.search.geo_context_service import GeoContextService
from flat_chat.search.service import SearchService

# Process-lifetime singleton — survives across requests, dies with the worker.
# Swap for a Postgres-backed store when persistence lands.
_session_store: SessionStore = InMemorySessionStore()


def get_session_store() -> SessionStore:
    return _session_store


def get_geo_context_service(
    db: Session = Depends(get_db),
) -> GeoContextService:
    return GeoContextService(db)


def get_search_service(
    db: Session = Depends(get_db),
    geo: GeoContextService = Depends(get_geo_context_service),
    embedder: Embedder | None = Depends(get_embedder),
) -> SearchService:
    return SearchService(db, geo, embedder)


def get_chat_service(
    search_service: SearchService = Depends(get_search_service),
    store: SessionStore = Depends(get_session_store),
):
    from flat_chat.chat.service import ChatService

    return ChatService(search_service=search_service, store=store)
