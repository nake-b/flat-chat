import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from flat_chat.api import agent, auth, bookmarks, chat, listings
from flat_chat.core.database import get_async_db
from flat_chat.core.dependencies import get_routing_service
from flat_chat.core.embedder import build_jina_embedder
from flat_chat.core.observability import (
    setup_logging,
    setup_observability,
    shutdown_observability,
)
from flat_chat.routing.motis import feed_window_stale
from flat_chat.routing.service import RoutingService

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


try:
    _API_VERSION = pkg_version("flat-chat-backend")
except PackageNotFoundError:  # not installed (e.g. bare source checkout)
    _API_VERSION = "0.0.0"

_DESCRIPTION = """\
Backend for the **Berlin Apartment AI Assistant** — a conversational
apartment-search app.

This is an internal backend-for-frontend (BFF): the routes here are consumed by
the project's own React SPA, not published as a public API. The two channels:

- **`POST /api/agent`** — AG-UI Protocol streaming (SSE). Owns *interpretation*:
  natural language → structured search, tool calls, and `SessionState` deltas.
  Not a JSON endpoint; it streams AG-UI events.
- **HTTP REST** (`/api/listings`, `/api/conversations`, `/api/bookmarks`, ...) —
  durable, cacheable reads and conversation/bookmark lifecycle.

Auth is a signed httpOnly JWT cookie (fastapi-users); most routes are
ownership-checked (a foreign resource returns 404, not 403).
"""

_OPENAPI_TAGS = [
    {"name": "auth", "description": "Login/logout (cookie) and user (`/me`) routes."},
    {
        "name": "conversations",
        "description": (
            "Conversation lifecycle: create, list (sidebar), history reload, "
            "`SessionState` snapshot (reload recovery), and hard delete."
        ),
    },
    {
        "name": "agent",
        "description": (
            "The AG-UI Protocol SSE endpoint. Streams text, tool-call lifecycle, "
            "and JSON-Patch state deltas — not a plain JSON response."
        ),
    },
    {
        "name": "listings",
        "description": (
            "Direct listing reads: tier-3 detail by id and batch tier-2 card "
            "hydration (`?ids=&view=card`). Browser-cacheable."
        ),
    },
    {
        "name": "bookmarks",
        "description": "Per-user saved listings (idempotent add/remove).",
    },
    {
        "name": "health",
        "description": "Liveness + optional gold-drift / transit-feed checks.",
    },
]

app = FastAPI(
    title="flat-chat API",
    version=_API_VERSION,
    description=_DESCRIPTION,
    openapi_tags=_OPENAPI_TAGS,
    lifespan=lifespan,
)

# Auth (fastapi-users) — login/logout (cookie) + the user routes (/me), under
# /api/auth. `get_user_id()` reads the cookie these set. No register router:
# accounts are seed-only (`scripts/seed_users.py` — see AUTH.md). Router wiring
# lives in `api/auth.py`, mirroring the other route modules.
app.include_router(
    auth.router,
    prefix="/api/auth",
    tags=["auth"],
)

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


@app.get("/api/health", tags=["health"])
async def health(
    extended: bool = False,
    db: AsyncSession = Depends(get_async_db),
    routing_service: RoutingService = Depends(get_routing_service),
):
    """Health check.

    Basic mode (no `?extended=true`): no DB hit. Returns `{"status": "ok"}`.

    Extended mode (`?extended=true`): includes a `gold_orphans` count —
    silver listings with no `listings_geo_context` row. Non-zero means
    silver landed but the gold ETL chain didn't (or failed for those
    rows); each orphan listing is invisible to every geo filter. Ops
    decide whether to fail; we just surface the number.

    Also reports `transit_feed` — the MOTIS timetable window ({first_day,
    last_day, stale}) so ops can spot a lapsed VBB feed (stale=true → the
    transit lens is clamping departures; re-run scripts/prep-routing.sh).
    `null` when MOTIS is unreachable / has no timetable loaded.
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

    # Best-effort transit-feed freshness (never fails the health check).
    window = await routing_service.feed_window()
    if window is not None:
        first, last = window
        transit_feed = {
            "first_day": first.isoformat(),
            "last_day": last.isoformat(),
            "stale": feed_window_stale(window),
        }
    else:
        transit_feed = None

    return {
        "status": "ok",
        "gold_orphans": int(orphans),
        "transit_feed": transit_feed,
    }
