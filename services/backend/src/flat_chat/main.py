import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from flat_chat.api import agent, bookmarks, chat, listings
from flat_chat.core.database import get_async_db
from flat_chat.core.embedder import build_jina_embedder
from flat_chat.core.observability import (
    setup_logging,
    setup_observability,
    shutdown_observability,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Logging first so subsequent lifespan steps and request handling
    # surface through our configured handler instead of disappearing.
    setup_logging()
    setup_observability()
    app.state.embedder = build_jina_embedder()
    yield
    shutdown_observability()


app = FastAPI(title="flat-chat API", lifespan=lifespan)

app.include_router(
    chat.router,
    prefix="/api/conversations",
    tags=["conversations"],
)

app.include_router(
    agent.router,
    prefix="/api/agent",
    tags=["agent"],
)

app.include_router(
    listings.router,
    prefix="/api/listings",
    tags=["listings"],
)

app.include_router(
    bookmarks.router,
    prefix="/api/bookmarks",
    tags=["bookmarks"],
)


@app.get("/api/health")
async def health(
    extended: bool = False,
    db: AsyncSession = Depends(get_async_db),
):
    """Health check.

    Basic mode (no `?extended=true`): no DB hit. Returns `{"status": "ok"}`.

    Extended mode (`?extended=true`): includes a `gold_orphans` count —
    silver listings with no `listings_geo_context` row. Non-zero means
    silver landed but the gold ETL chain didn't (or failed for those
    rows); each orphan listing is invisible to every geo filter. Ops
    decide whether to fail; we just surface the number.
    """
    if not extended:
        return {"status": "ok"}

    result = await db.execute(
        text(
            """
            SELECT COUNT(*) FROM world.listings l
            LEFT JOIN world.listings_geo_context lgc ON lgc.listing_id = l.id
            WHERE l.location IS NOT NULL AND lgc.listing_id IS NULL
            """
        )
    )
    orphans = result.scalar() or 0
    if orphans:
        logger.warning(
            "Gold drift: %d listings have no listings_geo_context row", orphans
        )
    return {"status": "ok", "gold_orphans": int(orphans)}
