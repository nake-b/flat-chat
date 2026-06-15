"""Embedding generation for listings.

Reads `listings.title + description`, computes a 1024-dim Jina v3
embedding, UPSERTs into `listings_embeddings`. Idempotent: re-running
overwrites only the rows whose model_name doesn't match the configured
one (or all, with --reembed).

Provider configuration: set `JINA_API_KEY` in env. Free tier covers
small backfills. The provider abstraction is intentionally thin — if you
need OpenAI / Cohere / a self-hosted model, replace `compute_embeddings`
with the equivalent batch call.

NOTE: First-time bootstrap doesn't need this module — the 0005 migration
copies existing `listings.embedding` data into `listings_embeddings`
on upgrade. This module is for: a) embedding listings that arrive
without one, b) re-embedding when swapping models.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable

import httpx
from sqlalchemy import Connection, text

logger = logging.getLogger(__name__)


# Locked in revision 0002 — the schema is `vector(1024)` so the model
# must match the dim. Changing models = new model_name + (eventually)
# a new schema.
MODEL_NAME: str = "jina-v3-1024"
EMBED_DIM: int = 1024

# Jina inference API. Free tier covers small backfills.
JINA_API_URL: str = "https://api.jina.ai/v1/embeddings"
JINA_MODEL_ID: str = "jina-embeddings-v3"

BATCH_SIZE: int = 64  # Jina free tier per-request cap


def _listing_text(title: str | None, description: str | None) -> str:
    """Compose the text we embed. Title first so it dominates short queries."""
    parts = [t for t in (title, description) if t]
    return "\n\n".join(parts) if parts else ""


def compute_embeddings(texts: list[str]) -> list[list[float]]:
    """Call the embedding provider for a batch. Returns one vector per input.

    Replace this function (or wire a different provider behind it) to swap
    models. The rest of the pipeline doesn't care which provider produced
    the vector — only the dim must match the schema.
    """
    api_key = os.environ.get("JINA_API_KEY")
    if not api_key:
        raise RuntimeError(
            "JINA_API_KEY not set — required for platinum.embed. "
            "Free-tier key from https://jina.ai/embeddings/."
        )
    response = httpx.post(
        JINA_API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={"model": JINA_MODEL_ID, "input": texts},
        timeout=60.0,
    )
    response.raise_for_status()
    payload = response.json()
    vectors = [item["embedding"] for item in payload["data"]]
    if any(len(v) != EMBED_DIM for v in vectors):
        raise RuntimeError(
            f"Provider returned dim != {EMBED_DIM} — check schema match."
        )
    return vectors


def _iter_pending(
    conn: Connection, *, reembed: bool, since: str | None
) -> Iterable[tuple[str, str]]:
    """Yield (listing_id, text) for listings that need (re-)embedding."""
    base = """
        SELECT l.id::text, l.title, l.description
        FROM listings l
    """
    clauses = []
    if not reembed:
        clauses.append("""
            NOT EXISTS (
                SELECT 1 FROM listings_embeddings le
                WHERE le.listing_id = l.id
                  AND le.model_name = :model
            )
        """)
    if since is not None:
        clauses.append("l.ingested_at >= :since")
    if clauses:
        base += " WHERE " + " AND ".join(clauses)

    rows = conn.execute(
        text(base),
        {"model": MODEL_NAME, "since": since} if since else {"model": MODEL_NAME},
    )
    for row in rows:
        text_val = _listing_text(row[1], row[2])
        if not text_val:
            continue
        yield (row[0], text_val)


def embed_pending(
    conn: Connection,
    *,
    reembed: bool = False,
    since: str | None = None,
    batch_size: int = BATCH_SIZE,
) -> tuple[int, int]:
    """UPSERT embeddings for listings that don't have one (or all if reembed).

    Returns (embedded, skipped). `skipped` counts listings with no text.
    """
    pending = list(_iter_pending(conn, reembed=reembed, since=since))
    if not pending:
        return 0, 0

    embedded = 0
    for i in range(0, len(pending), batch_size):
        batch = pending[i : i + batch_size]
        ids = [p[0] for p in batch]
        texts = [p[1] for p in batch]
        vectors = compute_embeddings(texts)
        for listing_id, vector in zip(ids, vectors, strict=True):
            conn.execute(
                text(
                    """
                    INSERT INTO listings_embeddings
                        (listing_id, embedding, model_name)
                    VALUES (:id, :vec, :model)
                    ON CONFLICT (listing_id) DO UPDATE
                    SET embedding = EXCLUDED.embedding,
                        model_name = EXCLUDED.model_name,
                        embedded_at = now()
                    """
                ),
                {"id": listing_id, "vec": str(vector), "model": MODEL_NAME},
            )
            embedded += 1
        logger.info(
            "embedded batch %d/%d (%d listings)",
            i // batch_size + 1,
            (len(pending) + batch_size - 1) // batch_size,
            len(batch),
        )

    return embedded, 0
