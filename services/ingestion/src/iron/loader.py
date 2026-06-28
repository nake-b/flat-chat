"""Iron loader — JSON replay for the card tier.

Reads a card-page JSON dump from disk and upserts into `iron_cards`.
The card JSON shape differs per source (kleinanzeigen uses
`external_id`/`listing_url`/`scraped_at`, wg-gesucht uses
`id`/`url`/`scrapedAt`) — this loader maps per-source.

`detail_scraped_at` is NEVER touched here, so re-loading a card JSON
does not undo any prior detail-scraper progress.

CLI:
    python -m iron.loader <path-to-cards.json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from db import get_session, get_table


def _row_to_iron(record: dict) -> dict:
    source = record.get("listing_source")

    if source == "kleinanzeigen":
        external_id = record.get("external_id")
        detail_url = record.get("listing_url")
        source_url = record.get("source_url")
        scraped_at = record.get("scraped_at")
    elif source == "wg-gesucht":
        external_id = record.get("id")
        detail_url = record.get("url")
        source_url = record.get("scrapeUrl")
        scraped_at = record.get("scrapedAt")
    else:
        raise ValueError(f"Unknown listing_source: {source!r}")

    if external_id is None or detail_url is None or scraped_at is None:
        raise ValueError(
            f"Missing required fields for source={source!r}: "
            f"external_id={external_id!r}, detail_url={detail_url!r}, "
            f"scraped_at={scraped_at!r}"
        )

    return {
        "source_name": source,
        "external_id": str(external_id),
        "detail_url": detail_url,
        "source_url": source_url,
        "data": record,
        "scraped_at": scraped_at,
    }


def load_json(path: Path, session: Session) -> int:
    """Upsert every record in `path` into iron_cards."""
    iron_cards = get_table("iron_cards")

    with open(path) as f:
        records = json.load(f)

    count = 0
    for record in records:
        values = _row_to_iron(record)
        stmt = pg_insert(iron_cards).values(**values)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_iron_source_external",
            set_={
                "data": stmt.excluded.data,
                "scraped_at": stmt.excluded.scraped_at,
                "detail_url": stmt.excluded.detail_url,
                "source_url": stmt.excluded.source_url,
                # NOTE: detail_scraped_at is intentionally NOT updated — replays
                # must not clobber prior detail-scraper progress.
            },
        )
        session.execute(stmt)
        count += 1

    session.commit()
    return count


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python -m iron.loader <path-to-cards.json>")
        sys.exit(2)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"ERROR: JSON file not found: {path}")
        sys.exit(1)

    session = get_session()
    try:
        print(f"Iron: loading from {path} ...")
        n = load_json(path, session)
        print(f"Iron: upserted {n} rows into iron_cards")
    finally:
        session.close()


if __name__ == "__main__":
    main()
