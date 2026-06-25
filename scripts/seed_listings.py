#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "psycopg[binary]>=3.2",
#     "httpx>=0.28",
# ]
# ///
"""
Seed the listings table from scraped JSON files.

Usage:
    uv run scripts/seed_listings.py

Env vars (with defaults for docker-compose dev):
    DATABASE_URL    postgresql://flat_chat:flat_chat@localhost:5432/flat_chat
    JINA_API_KEY    (required)
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psycopg

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://flat_chat:flat_chat@localhost:5432/flat_chat",
)
JINA_API_KEY = os.environ.get("JINA_API_KEY", "")
JINA_MODEL = "jina-embeddings-v3"
JINA_DIMENSIONS = 1024
JINA_BATCH_SIZE = 32
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "scraped"

DDL = """\
CREATE EXTENSION IF NOT EXISTS vector;

DROP TABLE IF EXISTS listings;

CREATE TABLE listings (
    id              UUID PRIMARY KEY,
    source          TEXT NOT NULL,
    source_listing_id TEXT NOT NULL,
    source_url      TEXT,
    title           TEXT,
    description     TEXT,
    price_warm_eur  NUMERIC,
    price_cold_eur  NUMERIC,
    nebenkosten_eur NUMERIC,
    kaution_eur     NUMERIC,
    area_sqm        NUMERIC,
    rooms           NUMERIC,
    floor           INTEGER,
    district        TEXT,
    postal_code     TEXT,
    address         TEXT,
    latitude        DOUBLE PRECISION,
    longitude       DOUBLE PRECISION,
    available_from  TEXT,
    available_until TEXT,
    listing_type    TEXT,
    features        JSONB DEFAULT '[]',
    images          JSONB DEFAULT '[]',
    raw             JSONB,
    description_embedding VECTOR(1024),
    scraped_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (source, source_listing_id)
);
"""

INSERT = """\
INSERT INTO listings (
    id, source, source_listing_id, source_url, title, description,
    price_warm_eur, price_cold_eur, nebenkosten_eur, kaution_eur,
    area_sqm, rooms, floor, district, postal_code, address,
    latitude, longitude, available_from, available_until,
    listing_type, features, images, raw,
    description_embedding, scraped_at
) VALUES (
    %(id)s, %(source)s, %(source_listing_id)s, %(source_url)s, %(title)s, %(description)s,
    %(price_warm_eur)s, %(price_cold_eur)s, %(nebenkosten_eur)s, %(kaution_eur)s,
    %(area_sqm)s, %(rooms)s, %(floor)s, %(district)s, %(postal_code)s, %(address)s,
    %(latitude)s, %(longitude)s, %(available_from)s, %(available_until)s,
    %(listing_type)s, %(features)s, %(images)s, %(raw)s,
    %(description_embedding)s, %(scraped_at)s
)
ON CONFLICT (source, source_listing_id) DO NOTHING
"""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_wggesucht(item: dict) -> dict:
    dump = item["dump"]
    card = dump.get("card", {})
    addr = dump.get("address", {})
    price = dump.get("price", {})
    avail = dump.get("availability", {})
    geo = dump.get("geo") or {}

    descriptions = dump.get("descriptions", [])
    desc_text = "\n\n".join(d.get("text", "") for d in descriptions)

    amenities = dump.get("amenities", [])
    feature_labels = [a["label"] for a in amenities if "label" in a]

    images = dump.get("images", [])
    if not images and card.get("imageUrl"):
        images = [card["imageUrl"]]

    rooms = dump.get("rooms") or card.get("rooms")

    floor_val = None
    for a in amenities:
        label = a.get("label", "")
        m = re.match(r"(\d+)\.\s*OG", label)
        if m:
            floor_val = int(m.group(1))
            break

    return {
        "id": str(uuid.uuid4()),
        "source": "wg-gesucht",
        "source_listing_id": str(item["id"]),
        "source_url": dump.get("canonicalUrl") or dump.get("url"),
        "title": dump.get("title") or card.get("title"),
        "description": desc_text,
        "price_warm_eur": price.get("warmmieteEur"),
        "price_cold_eur": price.get("kaltmieteEur"),
        "nebenkosten_eur": price.get("nebenkostenEur"),
        "kaution_eur": price.get("kautionEur"),
        "area_sqm": dump.get("areaSqm") or card.get("areaSqm"),
        "rooms": rooms,
        "floor": floor_val,
        "district": addr.get("district") or card.get("district"),
        "postal_code": addr.get("postalCode"),
        "address": addr.get("raw") or addr.get("street"),
        "latitude": geo.get("lat"),
        "longitude": geo.get("lng"),
        "available_from": avail.get("from"),
        "available_until": avail.get("until"),
        "listing_type": card.get("listingType", "rent"),
        "features": json.dumps(feature_labels),
        "images": json.dumps(images),
        "raw": json.dumps(dump),
        "scraped_at": item.get("scrapedAt"),
    }


def parse_sqm(raw: str | None) -> float | None:
    if not raw:
        return None
    m = re.search(r"([\d.,]+)", raw.replace(".", "").replace(",", "."))
    return float(m.group(1)) if m else None


def parse_kleinanzeigen(item: dict) -> dict:
    dump = item["dump"]
    card = dump.get("card", {})
    price = dump.get("price") or {}
    details = dump.get("details") or {}
    geo = dump.get("geo") or {}
    features = dump.get("features") or []

    locality = dump.get("locality", "")
    postal_match = re.match(r"(\d{5})", locality)
    postal_code = postal_match.group(1) if postal_match else None

    district = None
    if " - " in locality:
        district = locality.split(" - ", 1)[1].strip()
    elif postal_code and locality:
        rest = locality[len(postal_code):].strip()
        if rest:
            district = rest

    images_raw = dump.get("images", [])
    images = [img if isinstance(img, str) else img.get("url", "") for img in images_raw]

    rooms_raw = details.get("zimmer")
    rooms = float(rooms_raw) if rooms_raw else None

    floor_raw = details.get("etage")
    floor_val = int(floor_raw) if floor_raw and floor_raw.isdigit() else None

    price_text = card.get("price_text", "")
    cold_rent = price.get("coldRentEur")
    if cold_rent is None and price_text:
        m = re.search(r"([\d.]+)", price_text.replace(".", ""))
        if m:
            cold_rent = float(m.group(1))

    return {
        "id": str(uuid.uuid4()),
        "source": "kleinanzeigen",
        "source_listing_id": str(item["id"]),
        "source_url": dump.get("canonicalUrl") or dump.get("url"),
        "title": dump.get("title") or card.get("title"),
        "description": dump.get("description", ""),
        "price_warm_eur": price.get("warmmieteEur"),
        "price_cold_eur": cold_rent,
        "nebenkosten_eur": price.get("nebenkostenEur"),
        "kaution_eur": price.get("kautionEur"),
        "area_sqm": parse_sqm(details.get("wohnflaeche")) or card.get("areaSqm"),
        "rooms": rooms,
        "floor": floor_val,
        "district": district,
        "postal_code": postal_code,
        "address": locality,
        "latitude": geo.get("lat"),
        "longitude": geo.get("lng"),
        "available_from": details.get("verfuegbarAb"),
        "available_until": None,
        "listing_type": details.get("wohnungstyp", "rent"),
        "features": json.dumps(features),
        "images": json.dumps(images),
        "raw": json.dumps(dump),
        "scraped_at": item.get("scrapedAt"),
    }


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

def embedding_text(row: dict) -> str:
    parts = []
    if row["title"]:
        parts.append(row["title"])
    if row["description"]:
        parts.append(row["description"][:2000])
    features = json.loads(row["features"]) if row["features"] else []
    if features:
        parts.append("Features: " + ", ".join(features))
    if row["district"]:
        parts.append(f"District: {row['district']}")
    if row["rooms"]:
        parts.append(f"Rooms: {row['rooms']}")
    if row["area_sqm"]:
        parts.append(f"Area: {row['area_sqm']} sqm")
    if row["price_warm_eur"]:
        parts.append(f"Warm rent: {row['price_warm_eur']}€")
    return "\n".join(parts)


def fetch_embeddings(texts: list[str], api_key: str) -> list[list[float]]:
    resp = httpx.post(
        "https://api.jina.ai/v1/embeddings",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": JINA_MODEL,
            "input": texts,
            "dimensions": JINA_DIMENSIONS,
            "task": "retrieval.passage",
        },
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    data.sort(key=lambda x: x["index"])
    return [d["embedding"] for d in data]


def add_embeddings(rows: list[dict], api_key: str) -> None:
    texts = [embedding_text(r) for r in rows]
    total = len(texts)
    for start in range(0, total, JINA_BATCH_SIZE):
        end = min(start + JINA_BATCH_SIZE, total)
        batch = texts[start:end]
        print(f"  Embedding batch {start + 1}–{end} of {total} ...")
        embeddings = fetch_embeddings(batch, api_key)
        for i, emb in enumerate(embeddings):
            rows[start + i]["description_embedding"] = str(emb)
        if end < total:
            time.sleep(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_file(path: Path, parser) -> list[dict]:
    with open(path) as f:
        items = json.load(f)
    print(f"  Loaded {len(items)} items from {path.name}")
    return [parser(item) for item in items]


def main() -> None:
    if not JINA_API_KEY:
        print("ERROR: Set JINA_API_KEY environment variable", file=sys.stderr)
        sys.exit(1)

    print("Loading scraped data ...")
    rows: list[dict] = []
    wg_path = DATA_DIR / "wggesucht-detail.json"
    ka_path = DATA_DIR / "kleinanzeigen-detail.json"
    if wg_path.exists():
        rows.extend(load_file(wg_path, parse_wggesucht))
    if ka_path.exists():
        rows.extend(load_file(ka_path, parse_kleinanzeigen))

    if not rows:
        print("No data found. Exiting.")
        sys.exit(1)

    print(f"\nTotal: {len(rows)} listings")

    print("\nGenerating embeddings via Jina v3 ...")
    add_embeddings(rows, JINA_API_KEY)

    print(f"\nConnecting to Postgres at {DATABASE_URL} ...")
    with psycopg.connect(DATABASE_URL) as conn:
        print("Creating listings table ...")
        conn.execute(DDL)
        conn.commit()

        print(f"Inserting {len(rows)} listings ...")
        with conn.cursor() as cur:
            for row in rows:
                cur.execute(INSERT, row)
        conn.commit()

        count = conn.execute("SELECT count(*) FROM listings").fetchone()[0]
        print(f"\nDone! {count} listings in the database.")

        sample = conn.execute(
            "SELECT source, source_listing_id, title, price_warm_eur, district "
            "FROM listings LIMIT 5"
        ).fetchall()
        print("\nSample rows:")
        for r in sample:
            print(f"  [{r[0]}] {r[2][:60]}  |  {r[3]}€  |  {r[4]}")


if __name__ == "__main__":
    main()
