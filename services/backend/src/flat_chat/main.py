from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from flat_chat.api import chat
from flat_chat.core.embedder import build_jina_embedder
from flat_chat.core.observability import setup_observability


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_observability()
    app.state.embedder = build_jina_embedder()
    yield


app = FastAPI(title="flat-chat API", lifespan=lifespan)

app.include_router(
    chat.router,
    prefix="/api/conversations",
    tags=["conversations"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}
