"""Backfill the `embedding` column on `listings` via Jina v3.

Silver-transformers populate everything *except* the embedding so the cost
of calling Jina doesn't sit on every transform. This module is the one-shot
backfill: it selects rows where `embedding IS NULL`, embeds their text in
batches, and writes the vectors back. Idempotent — running twice is a
no-op for already-embedded rows.

Usage:
    python -m silver.embed [--batch-size N] [--limit M] [--dry-run]

When a DB refresh lands new rows (via `scripts/refresh-db.sh`), re-run this
after `silver.run`. We'll fold the call into the pipeline once we trust the
throughput.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import httpx
from sqlalchemy import select, update
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from db import get_session, get_table

logger = logging.getLogger(__name__)

# Status codes we retry on. 429 = rate limit (Jina's free tier especially);
# 5xx = transient server-side. Everything else (incl. 4xx auth / bad-request)
# is a real problem we want to surface, not paper over with retries.
_RETRIABLE_STATUS = {429, 500, 502, 503, 504}

# Status codes that mean every batch will fail the same way — keep retrying
# them would burn the cron silently. Re-raised out of the outer loop so the
# process exits non-zero and cron alerts.
_TERMINAL_STATUS = {401, 403}


def _is_retriable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRIABLE_STATUS
    # httpx.ConnectError / ReadTimeout / RemoteProtocolError etc. are all
    # subclasses of httpx.TransportError — retry these too.
    return isinstance(exc, httpx.TransportError)


def _is_terminal_auth(exc: BaseException) -> bool:
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in _TERMINAL_STATUS
    )


# Jina v3 — 1024 dims, 8K token window. `retrieval.passage` is the listing-
# side LoRA; the query side uses `retrieval.query` in the backend's search
# service. Keeping the two task-side values aligned is what gives the
# bi-encoder pair its asymmetric quality boost.
JINA_MODEL = "jina-embeddings-v3"
JINA_TASK = "retrieval.passage"
JINA_BASE_URL_DEFAULT = "https://api.jina.ai/v1"

# Truncate the embed-source text well under Jina's 8K token limit. Listings
# rarely exceed this; the cap is a safety net against pathological titles +
# descriptions concatenated together.
_MAX_TEXT_CHARS = 6000


def _build_text(row: dict) -> str:
    """Concatenate listing text fields into a single embed payload.

    Title + description carry most of the semantic signal; amenity labels
    (when present) anchor specific must-have features in the embedding
    space ("Balkon", "Aufzug") so phrasing in the user's query matches.
    """
    parts: list[str] = []
    if row.get("title"):
        parts.append(str(row["title"]).strip())
    if row.get("description"):
        parts.append(str(row["description"]).strip())
    features = row.get("features")
    if isinstance(features, list) and features:
        labels = [str(f).strip() for f in features if f]
        if labels:
            parts.append("Features: " + ", ".join(labels))
    text = "\n\n".join(p for p in parts if p)
    return text[:_MAX_TEXT_CHARS]


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential_jitter(initial=1, max=30),
    retry=retry_if_exception(_is_retriable),
    reraise=True,
)
def _embed_batch(
    client: httpx.Client, texts: list[str], api_key: str
) -> list[list[float]]:
    """POST a batch of texts to Jina /v1/embeddings and return their vectors.

    Retries on 429 / 5xx and transport errors with exponential backoff +
    jitter (up to 5 attempts, max 30 s wait). Non-retriable errors propagate
    so the outer loop can decide whether to skip the batch.
    """
    response = client.post(
        "/embeddings",
        json={"model": JINA_MODEL, "task": JINA_TASK, "input": texts},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=60.0,
    )
    response.raise_for_status()
    data = response.json()
    # Jina returns {"data": [{"index": i, "embedding": [...]}, ...]} — keep
    # the order stable by sorting on index, since the API doesn't guarantee
    # response order matches input order across all model versions.
    items = sorted(data["data"], key=lambda x: x["index"])
    return [item["embedding"] for item in items]


def backfill_embeddings(
    *, batch_size: int = 32, limit: int | None = None, dry_run: bool = False
) -> int:
    """Embed every row where embedding IS NULL. Returns the count embedded."""
    api_key = os.environ.get("JINA_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "JINA_API_KEY is unset. Set it in .env (forwarded by docker compose) "
            "before running silver.embed."
        )
    base_url = os.environ.get("JINA_BASE_URL", JINA_BASE_URL_DEFAULT).rstrip("/")

    session = get_session()
    listings = get_table("listings")

    # Cursor over listings.id so the loop is guaranteed monotonic. Without it,
    # a batch where every row has no embeddable text re-selects the same rows
    # on the next iteration (embedding stays NULL) and the loop spins forever.
    # ORDER BY id ASC + `id > last_id` advances past the skipped rows.
    last_id: str | None = None
    total_embedded = 0
    try:
        with httpx.Client(base_url=base_url) as client:
            while True:
                batch_limit = (
                    min(batch_size, limit - total_embedded)
                    if limit is not None
                    else batch_size
                )
                if batch_limit <= 0:
                    break

                stmt = select(
                    listings.c.id,
                    listings.c.title,
                    listings.c.description,
                    listings.c.features,
                ).where(listings.c.embedding.is_(None))
                if last_id is not None:
                    stmt = stmt.where(listings.c.id > last_id)
                stmt = stmt.order_by(listings.c.id).limit(batch_limit)
                rows = session.execute(stmt).mappings().all()

                if not rows:
                    break
                last_id = rows[-1]["id"]

                texts = [_build_text(dict(r)) for r in rows]
                # Skip rows with no embeddable text — they'd produce a useless
                # zero-content vector and confuse cosine ranking. The cursor
                # above already advanced past them, so the next iteration
                # picks up fresh rows instead of looping.
                payload_rows = [(r, t) for r, t in zip(rows, texts, strict=True) if t]
                if not payload_rows:
                    logger.warning(
                        "Skipping batch of %d rows: no embeddable text", len(rows)
                    )
                    continue

                if dry_run:
                    logger.info(
                        "[dry-run] would embed %d rows (first id: %s)",
                        len(payload_rows),
                        payload_rows[0][0]["id"],
                    )
                    total_embedded += len(payload_rows)
                    continue

                try:
                    vectors = _embed_batch(
                        client, [t for _, t in payload_rows], api_key
                    )
                except httpx.HTTPError as exc:
                    # Auth failures will hit every batch the same way — keep
                    # retrying them would silently burn the cron. Re-raise so
                    # the process exits non-zero.
                    if _is_terminal_auth(exc):
                        raise
                    logger.error(
                        "Jina request failed (%s): skipping batch of %d",
                        exc,
                        len(payload_rows),
                    )
                    # Move on — re-running silver.embed picks up the skipped
                    # rows on the next pass. Don't crash mid-backfill.
                    continue

                for (row, _), vector in zip(payload_rows, vectors, strict=True):
                    session.execute(
                        update(listings)
                        .where(listings.c.id == row["id"])
                        .values(embedding=vector)
                    )
                session.commit()
                total_embedded += len(payload_rows)
                logger.info(
                    "Embedded %d rows (running total: %d)",
                    len(payload_rows),
                    total_embedded,
                )

                if limit is not None and total_embedded >= limit:
                    break
    finally:
        session.close()

    return total_embedded


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap on rows embedded this run (default: embed all).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan only — count rows that would be embedded; don't call Jina.",
    )
    args = parser.parse_args()

    try:
        n = backfill_embeddings(
            batch_size=args.batch_size, limit=args.limit, dry_run=args.dry_run
        )
    except RuntimeError as exc:
        logger.error("Silver embed: %s", exc)
        sys.exit(1)

    verb = "would embed" if args.dry_run else "embedded"
    logger.info("Silver embed: %s %d listing(s)", verb, n)


if __name__ == "__main__":
    main()
