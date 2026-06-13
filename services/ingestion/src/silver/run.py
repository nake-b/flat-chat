"""CLI: `python -m silver.run` — transform bronze rows into listings."""

from db import get_session

from .transformer import transform


def main() -> None:
    session = get_session()
    try:
        print("Silver: transforming bronze rows into listings ...")
        n = transform(session)
        print(f"Silver: upserted {n} rows into listings")
    finally:
        session.close()


if __name__ == "__main__":
    main()
