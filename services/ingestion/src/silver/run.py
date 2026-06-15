"""CLI: `python -m silver.run` — transform bronze rows into listings."""

import logging

from db import get_session

from .transformer import transform

logger = logging.getLogger(__name__)


def main() -> None:
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


if __name__ == "__main__":
    main()
