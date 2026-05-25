"""FastAPI app entrypoint.

Process-wide setup (observability, embedder) happens in the `lifespan`
context manager — never at module import. Importing `flat_chat.main`
from a test or a script should be free of side effects. See CLAUDE.md
§Architecture Notes "No side effects at module import" for the rule.

Routers are mounted below; runtime behavior for the streaming `/api/agent`
route is documented in `agent-compound-docs/decisions/chat-runtime-and-streaming.md`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from flat_chat.api import agent, chat
from flat_chat.core.embedder import build_jina_embedder
from flat_chat.core.observability import setup_observability, shutdown_observability


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
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


@app.get("/api/health")
def health():
    return {"status": "ok"}
