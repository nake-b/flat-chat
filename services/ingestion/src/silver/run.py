"""CLI: `python -m silver.run` — transform bronze rows into listings AND run context ingestion."""

from db import get_session
from .transformer import transform

# NEU: dein Kontext‑Ingestion‑Script importieren
from context_ingestion.ingest_context import main as context_main


def main() -> None:
    # --- Step 1: Silver-Pipeline ---
    session = get_session()
    try:
        print("Silver: transforming bronze rows into listings ...")
        n = transform(session)
        print(f"Silver: upserted {n} rows into listings")
    finally:
        session.close()

    # --- Step 2: Context-Ingestion ---
    print("Context: running ingestion_context.py ...")
    context_main()
    print("Context: ingestion completed.")


if __name__ == "__main__":
    main()
