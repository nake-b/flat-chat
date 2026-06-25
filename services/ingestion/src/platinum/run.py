"""CLI orchestrator for the platinum layer (`listings_embeddings`).

Usage:
    python -m platinum.run                         # embed missing only
    python -m platinum.run --reembed               # re-embed everything
    python -m platinum.run --since 2026-06-01      # only listings since date

Commits per batch (see `embed.py:embed_pending`): the embedding UPSERTs are
idempotent, so a mid-run failure keeps completed batches and the next run
resumes from where it stopped. Embedding generation calls the Jina v3 API
(free tier, with retry/backoff); provider details in `embed.py`.
"""

from __future__ import annotations

import argparse
import logging
import sys
import traceback

from db import engine

from . import embed as platinum

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="platinum.run",
        description="Generate embeddings for listings (platinum layer).",
    )
    parser.add_argument(
        "--reembed",
        action="store_true",
        help="re-embed every listing, even those already in listings_embeddings",
    )
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        help="only embed listings ingested on or after this ISO date",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    try:
        # Commit-as-you-go (NOT engine.begin's begin-once): embed_pending
        # commits each batch so a mid-run failure preserves completed work.
        with engine.connect() as conn:
            embedded, skipped = platinum.embed_pending(
                conn, reembed=args.reembed, since=args.since
            )
        logger.info(
            "platinum: %d embedded, %d skipped (no text)", embedded, skipped
        )
        return 0
    except Exception:
        logger.error(
            "FAIL platinum (completed batches kept):\n%s", traceback.format_exc()
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
