"""Shared parsing helpers for silver transformers.

These functions are pure — no DB, no IO — and safe to unit-test.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable

GERMAN_MONTHS = {
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4,
    "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9,
    "oktober": 10, "november": 11, "dezember": 12,
}


def parse_german_date(s: str | None) -> datetime | None:
    """'01.09.2026' (DD.MM.YYYY) -> datetime. None-safe."""
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%d.%m.%Y")
    except ValueError:
        return None


def parse_german_month_year(s: str | None) -> datetime | None:
    """'April 2026' -> datetime(2026, 4, 1). 'Sofort' -> None."""
    if not s:
        return None
    parts = s.strip().split()
    if len(parts) != 2:
        return None
    month_name, year_str = parts
    month = GERMAN_MONTHS.get(month_name.lower())
    if month is None:
        return None
    try:
        return datetime(int(year_str), month, 1)
    except ValueError:
        return None


def parse_sqm(s: str | int | float | None) -> float | None:
    """'72,50 m²' -> 72.5. Accepts ints/floats unchanged."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    m = re.search(r"(\d+(?:[.,]\d+)?)", str(s))
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


def parse_int_str(s: str | int | float | None) -> int | None:
    """'2' -> 2; None-safe; floats truncated."""
    if s is None or s == "":
        return None
    if isinstance(s, int):
        return s
    if isinstance(s, float):
        return int(s)
    m = re.search(r"-?\d+", str(s))
    return int(m.group(0)) if m else None


def parse_float_str(s: str | int | float | None) -> float | None:
    if s is None or s == "":
        return None
    if isinstance(s, (int, float)):
        return float(s)
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(s))
    if not m:
        return None
    return float(m.group(0).replace(",", "."))


def parse_postal_district(locality: str | None) -> tuple[str | None, str | None]:
    """'13353 Mitte - Wedding' -> ('13353', 'Mitte - Wedding')."""
    if not locality:
        return None, None
    m = re.match(r"\s*(\d{5})\s*(.*)$", locality.strip())
    if not m:
        return None, locality.strip() or None
    postal = m.group(1)
    rest = m.group(2).strip() or None
    return postal, rest


def parse_floor_label(label: str | None) -> int | None:
    """'5. OG' -> 5, 'EG'/'Erdgeschoss' -> 0, 'höher als 5. OG' -> None, 'Tiefparterre' -> -1."""
    if not label:
        return None
    s = label.strip().lower()
    if "höher als" in s or "hoeher als" in s:
        return None
    if s in {"eg", "erdgeschoss"}:
        return 0
    if "tiefparterre" in s or "souterrain" in s:
        return -1
    if "dachgeschoss" in s:
        # Unknown numeric floor; leave None, but caller may set apartment_type instead.
        return None
    m = re.match(r"(\d+)\.\s*og", s)
    if m:
        return int(m.group(1))
    return None


_ENERGY_CLASS_RE = re.compile(r"Energieeffizienzklasse\s+([A-H][+\-]?)", re.IGNORECASE)
_BAUJAHR_RE = re.compile(r"Baujahr\s+(\d{4})", re.IGNORECASE)
_ENERGY_VALUE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*kWh", re.IGNORECASE)


def parse_energy_label(label: str) -> dict:
    """Extracts energy fields from a comma-separated amenity string like
    'Bedarfsausweis, Gas, Baujahr 1975, Energieeffizienzklasse A+'.

    Returns a dict with any of: energy_pass_type, main_energy_source,
    construction_year, energy_class, energy_consumption_kwh.
    """
    out: dict = {}
    if not label:
        return out

    parts = [p.strip() for p in label.split(",")]
    for part in parts:
        pl = part.lower()
        if "bedarfsausweis" in pl:
            out["energy_pass_type"] = "Bedarfsausweis"
        elif "verbrauchsausweis" in pl:
            out["energy_pass_type"] = "Verbrauchsausweis"
        elif pl in {"gas", "öl", "oel", "strom", "fernwärme", "fernwaerme", "kohle", "holz", "pellets", "solar"}:
            out["main_energy_source"] = part.title()
        m = _BAUJAHR_RE.search(part)
        if m:
            out["construction_year"] = int(m.group(1))
        m = _ENERGY_CLASS_RE.search(part)
        if m:
            out["energy_class"] = m.group(1).upper()
        m = _ENERGY_VALUE_RE.search(part)
        if m:
            out["energy_consumption_kwh"] = float(m.group(1).replace(",", "."))

    return out


def amenity_match(items: Iterable[str] | None, *needles: str) -> bool:
    """Case-insensitive substring search across a list of strings.

    Returns True if any needle is a substring of any item.
    """
    if not items:
        return False
    lowered = [str(i).lower() for i in items]
    for needle in needles:
        n = needle.lower()
        for item in lowered:
            if n in item:
                return True
    return False


def find_amenity(items: Iterable[str] | None, *needles: str) -> str | None:
    """Return the first item that contains any needle, else None."""
    if not items:
        return None
    for item in items:
        item_str = str(item)
        item_lower = item_str.lower()
        for needle in needles:
            if needle.lower() in item_lower:
                return item_str
    return None


# Berlin's actual bounding box (Senate Department for Urban Development).
# A generous envelope — Berlin proper sits inside 13.088–13.761 / 52.339–52.675;
# we widen by ~0.01° on each side to forgive listings sitting fractionally
# outside the official border (Brandenburg-side commuter towns get filtered
# out anyway by `city = "Berlin"`).
_BERLIN_LON_MIN, _BERLIN_LON_MAX = 13.08, 13.77
_BERLIN_LAT_MIN, _BERLIN_LAT_MAX = 52.33, 52.68


def clean_berlin_coords(
    lat: float | int | None, lng: float | int | None
) -> tuple[float | None, float | None]:
    """Pass through (lat, lng) only if they look like Berlin coordinates.

    Returns (None, None) for null-island sentinels (0, 0), swapped pairs
    (lon-as-lat etc.), and points outside Berlin's bounding box. Scrapers
    occasionally emit 0/0 when the source dump lacks geocoding — letting
    those through pollutes geo-context queries (a 0/0 listing's "nearest"
    school / hospital / noise sample is ~5,930 km away, which the KNN
    operator still happily returns).
    """
    if lat is None or lng is None:
        return None, None
    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except (TypeError, ValueError):
        return None, None
    if not (_BERLIN_LAT_MIN <= lat_f <= _BERLIN_LAT_MAX):
        return None, None
    if not (_BERLIN_LON_MIN <= lng_f <= _BERLIN_LON_MAX):
        return None, None
    return lat_f, lng_f


def map_lister_type(raw: str | None) -> str | None:
    """Normalize lister/seller type strings across sources.

    wg-gesucht: 'private' / 'agency'
    kleinanzeigen: 'Privater Nutzer' / 'Gewerblicher Nutzer'
    -> 'private' / 'commercial' / 'agency'
    """
    if not raw:
        return None
    s = raw.strip().lower()
    if "privat" in s:
        return "private"
    if "agency" in s or "agentur" in s or "gewerb" in s or "commercial" in s:
        return "commercial"
    return s or None
