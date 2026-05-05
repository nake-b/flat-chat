from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import conversations

app = FastAPI(title="flat-chat API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(
    conversations.router,
    prefix="/api/conversations",
    tags=["conversations"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}
