"""CLI: `python -m silver.run` — transform bronze rows into listings.

Chains gold + platinum at the end so a single silver run produces fully
enriched + embedded listings ready for search. Failures in the chained
stages don't roll back silver (they're separate transactions) — silver
having the truth is the precondition for both downstream layers, and a
fresh-but-not-yet-enriched listings table is a recoverable intermediate
state.
"""

import logging
import sys

from db import get_session

from .transformer import transform

logger = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    session = get_session()
    try:
        logger.info("Silver: transforming bronze rows into listings ...")
        n = transform(session)
        logger.info("Silver: upserted %d rows into listings", n)
    finally:
        session.close()

    # Chain gold enrichment for any new listings — runs all chip families.
    # Imported here (not at top) so silver can run with an incomplete
    # ingestion service installation; gold has its own deps (e.g. uses
    # raw SQL only). Same logic applies to platinum.
    logger.info("Silver: chaining gold enrichment ...")
    from gold.run import main as gold_main

    gold_rc = gold_main([])
    if gold_rc != 0:
        logger.warning("Silver→gold chain returned non-zero (%d)", gold_rc)

    logger.info("Silver: chaining platinum embedding (best-effort) ...")
    try:
        from platinum.run import main as platinum_main

        platinum_rc = platinum_main([])
        if platinum_rc != 0:
            logger.warning(
                "Silver→platinum chain returned non-zero (%d) — "
                "semantic search falls back to recency",
                platinum_rc,
            )
    except Exception as exc:
        # Platinum requires a provider API key. Don't fail the whole
        # silver run just because embeddings can't be generated — gold
        # still works, structured search still works.
        logger.warning(
            "Silver→platinum chain skipped: %s. "
            "Set JINA_API_KEY to enable embedding backfill.",
            exc,
        )

    # Return code reflects silver itself, not the chained stages — they
    # log their own status. Silver's job is upserting listings; if that
    # worked, exit 0.
    return 0


if __name__ == "__main__":
    sys.exit(main())
