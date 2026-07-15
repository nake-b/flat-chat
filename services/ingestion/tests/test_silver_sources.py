"""Unit tests for silver `lister_type` derivation after the PII refactor.

The scrapers now emit ONLY the lister `type` (no name). These tests pin that the
silver transformers still derive `lister_type` correctly from a `type`-only
payload — i.e. the privacy change didn't break the one poster-related field we
intentionally keep. Pure functions, no DB.
"""

from __future__ import annotations

from silver.sources.housinganywhere import to_listing_row as ha_row
from silver.sources.kleinanzeigen import to_listing_row as ka_row
from silver.sources.wg_gesucht import to_listing_row as wg_row


def test_kleinanzeigen_lister_type_from_type_only() -> None:
    private = ka_row({"data": {"dump": {"seller": {"type": "Privater Nutzer"}}}})
    commercial = ka_row({"data": {"dump": {"seller": {"type": "Gewerblicher Nutzer"}}}})
    assert private["lister_type"] == "private"
    assert commercial["lister_type"] == "commercial"


def test_wg_gesucht_lister_type_from_type_only() -> None:
    private = wg_row({"data": {"dump": {"lister": {"type": "private"}}}})
    agency = wg_row({"data": {"dump": {"lister": {"type": "agency"}}}})
    assert private["lister_type"] == "private"
    assert agency["lister_type"] == "commercial"


def test_housinganywhere_lister_type_from_type_only() -> None:
    dump = {"entity": {"id": "1", "facilities": {}, "price": 86500}}
    private = ha_row({"data": {"dump": {**dump, "advertiser": {"type": "private"}}}})
    agency = ha_row({"data": {"dump": {**dump, "advertiser": {"type": "agency"}}}})
    assert private["lister_type"] == "private"
    assert agency["lister_type"] == "commercial"


def test_description_is_redacted_in_silver() -> None:
    row = ka_row(
        {"data": {"dump": {"description": "Schöne Wohnung. Tel: 0151 2637 6735"}}}
    )
    assert "0151" not in row["description"]
    assert "[redacted]" in row["description"]
