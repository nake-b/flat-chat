import sys
from pathlib import Path

from bronze.loader import load_json
from db import get_session
from silver.transformer import transform

# Default path to scraped JSON (relative to the workdir /app in Docker)
DEFAULT_JSON = Path("src/scraper/wohninberlin/wohninberlin.json")


def main():
    json_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_JSON

    if not json_path.exists():
        print(f"ERROR: JSON file not found: {json_path}")
        sys.exit(1)

    session = get_session()
    try:
        print(f"Loading bronze from {json_path} ...")
        bronze_count = load_json(json_path, session)
        print(f"Bronze: upserted {bronze_count} rows into raw_listings")

        print("Transforming bronze → silver ...")
        silver_count = transform(session)
        print(f"Silver: upserted {silver_count} rows into listings")
    finally:
        session.close()

    print("Done.")


if __name__ == "__main__":
    main()
