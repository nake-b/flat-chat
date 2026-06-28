"""One-off (idempotent) scrub of poster PII already stored in the DB.

The scrapers + loader sanitizer (``pii.strip_pii``) stop NEW poster PII from
being collected/persisted. This script removes PII that accumulated in the DB
*before* those guards existed:

  - ``raw_listings.data`` (bronze) — via ``strip_pii(..., tier="bronze")``
  - ``iron_cards.data``   (iron)   — via ``strip_pii(..., tier="iron")``
  - ``listings.description``       — via ``redact_freetext`` (phone/email/WhatsApp)

It reuses the exact same spec as the loaders, so "what counts as PII" has a
single source of truth. Idempotent: a row is only rewritten when scrubbing
actually changes it, so re-runs are no-ops.

Usage (from the ingestion service):
    uv run python -m scripts.scrub_poster_pii [--dry-run] [--batch-size N]
"""

from __future__ import annotations

import argparse
import copy

from sqlalchemy import select

from db import engine, get_table
from pii import strip_pii
from silver.sources.common import redact_freetext


def _scrub_jsonb(table_name: str, tier: str, *, dry_run: bool, batch_size: int) -> dict:
    """Strip PII from a JSONB `data` column. Returns per-source change counts."""
    table = get_table(table_name)
    counts: dict[str, int] = {}
    scanned = 0

    with engine.connect() as conn:
        rows = conn.execute(
            select(table.c.id, table.c.source_name, table.c.data)
        ).fetchall()

        pending = 0
        for row in rows:
            scanned += 1
            source = row.source_name or "unknown"
            cleaned = strip_pii(copy.deepcopy(row.data), source, tier)
            if cleaned == row.data:
                continue
            counts[source] = counts.get(source, 0) + 1
            if not dry_run:
                conn.execute(
                    table.update().where(table.c.id == row.id).values(data=cleaned)
                )
                pending += 1
                if pending >= batch_size:
                    conn.commit()
                    pending = 0
        if not dry_run and pending:
            conn.commit()

    changed = sum(counts.values())
    verb = "would change" if dry_run else "changed"
    print(f"{table_name}: scanned {scanned}, {verb} {changed} {dict(counts)}")
    return counts


def _scrub_descriptions(*, dry_run: bool, batch_size: int) -> int:
    """Redact contact info from `listings.description`. Returns changed count."""
    listings = get_table("listings")
    changed = 0
    scanned = 0

    with engine.connect() as conn:
        rows = conn.execute(
            select(listings.c.id, listings.c.description).where(
                listings.c.description.is_not(None)
            )
        ).fetchall()

        pending = 0
        for row in rows:
            scanned += 1
            cleaned = redact_freetext(row.description)
            if cleaned == row.description:
                continue
            changed += 1
            if not dry_run:
                conn.execute(
                    listings.update()
                    .where(listings.c.id == row.id)
                    .values(description=cleaned)
                )
                pending += 1
                if pending >= batch_size:
                    conn.commit()
                    pending = 0
        if not dry_run and pending:
            conn.commit()

    verb = "would redact" if dry_run else "redacted"
    print(f"listings.description: scanned {scanned}, {verb} {changed}")
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="rows per commit (default: 500).",
    )
    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "SCRUBBING"
    print(f"=== Poster-PII scrub [{mode}] ===")
    opts = {"dry_run": args.dry_run, "batch_size": args.batch_size}
    _scrub_jsonb("raw_listings", "bronze", **opts)
    _scrub_jsonb("iron_cards", "iron", **opts)
    _scrub_descriptions(**opts)
    print("Done.")


if __name__ == "__main__":
    main()
