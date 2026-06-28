"""Bronze loader — JSON replay for the detail-page tier.

Reads a detail-page JSON dump from disk and upserts into `raw_listings`.
Also links to the matching iron card row when one exists.

CLI:
    python -m bronze.loader <path-to-detail.json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from db import get_session, get_table


def _lookup_iron_card_id(
    session: Session, source_name: str, external_id: str
) -> str | None:
    iron = get_table("iron_cards")
    stmt = select(iron.c.id).where(
        iron.c.source_name == source_name,
        iron.c.external_id == external_id,
    )
    result = session.execute(stmt).scalar_one_or_none()
    return result


def load_json(path: Path, session: Session) -> int:
    """Upsert every record in `path` into raw_listings.

    When a matching iron_cards row exists, link via iron_card_id AND flip its
    detail_scraped_at cursor so the live detail scraper skips it on the next
    run.
    """
    raw_listings = get_table("raw_listings")
    iron_cards = get_table("iron_cards")

    with open(path) as f:
        records = json.load(f)

    count = 0
    for record in records:
        source_name = record.get("listing_source", "unknown")
        external_id = str(record["id"])
        iron_card_id = _lookup_iron_card_id(session, source_name, external_id)

        values = {
            "source_name": source_name,
            "source_url": record.get("scrapeUrl"),
            "external_id": external_id,
            "data": record,
            "scraped_at": record["scrapedAt"],
            "iron_card_id": iron_card_id,
        }

        stmt = pg_insert(raw_listings).values(**values)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_raw_source_external",
            set_={
                "data": stmt.excluded.data,
                "scraped_at": stmt.excluded.scraped_at,
                "source_url": stmt.excluded.source_url,
                "iron_card_id": stmt.excluded.iron_card_id,
            },
        )
        session.execute(stmt)
        count += 1

        if iron_card_id is not None:
            session.execute(
                iron_cards.update()
                .where(iron_cards.c.id == iron_card_id)
                .where(iron_cards.c.detail_scraped_at.is_(None))
                .values(detail_scraped_at=record["scrapedAt"])
            )

    session.commit()
    return count


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python -m bronze.loader <path-to-detail.json>")
        sys.exit(2)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"ERROR: JSON file not found: {path}")
        sys.exit(1)

    session = get_session()
    try:
        print(f"Bronze: loading from {path} ...")
        n = load_json(path, session)
        print(f"Bronze: upserted {n} rows into raw_listings")
    finally:
        session.close()


if __name__ == "__main__":
    main()
