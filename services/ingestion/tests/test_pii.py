"""Unit tests for the poster-PII sanitizer + free-text redaction.

`strip_pii` is the loader choke-point that keeps poster identity (name, phone,
profile URL, member/active-since, online-status, embedded-state blobs) out of
the bronze/iron JSONB; only the non-identifying `type` survives. `redact_freetext`
strips contact info pasted into listing descriptions. Both are pure — no DB.
"""

from __future__ import annotations

import copy

from pii import strip_pii
from silver.sources.common import redact_freetext

# --- strip_pii: bronze ------------------------------------------------------


def test_bronze_kleinanzeigen_keeps_only_seller_type() -> None:
    record = {
        "dump": {
            "title": "Helles 2-Zi",
            "seller": {
                "name": "Hans Müller",
                "type": "Privater Nutzer",
                "activeSince": "Aktiv seit 2019",
                "phone": "0151 26376735",
            },
            "embeddedState": ["window.__INITIAL_STATE__ = {...}"],
        }
    }
    out = strip_pii(record, "kleinanzeigen", "bronze")
    assert out["dump"]["seller"] == {"type": "Privater Nutzer"}
    assert "embeddedState" not in out["dump"]
    assert out["dump"]["title"] == "Helles 2-Zi"  # non-PII untouched


def test_bronze_wg_gesucht_keeps_only_lister_type() -> None:
    record = {
        "dump": {
            "lister": {
                "name": "E. Peics",
                "type": "private",
                "memberSince": "Mitglied seit 2021",
                "online": "Online: 3 hours",
                "verified": True,
            }
        }
    }
    out = strip_pii(record, "wg-gesucht", "bronze")
    assert out["dump"]["lister"] == {"type": "private"}


def test_bronze_housinganywhere_reduces_advertiser_and_strips_entity_identity() -> None:
    record = {
        "dump": {
            "advertiser": {"id": 42, "name": "Jane", "type": "agency", "photo": "u"},
            "entity": {"id": 7, "price": 86500, "advertiser": {"name": "Jane"}},
        }
    }
    out = strip_pii(record, "housinganywhere", "bronze")
    assert out["dump"]["advertiser"] == {"type": "agency"}
    assert "advertiser" not in out["dump"]["entity"]
    assert out["dump"]["entity"]["price"] == 86500  # listing facts untouched


# --- strip_pii: iron --------------------------------------------------------


def test_iron_wg_gesucht_strips_top_level_poster_fields() -> None:
    record = {"posterName": "E. Peics", "onlineSince": "3 hours", "title": "Cosy"}
    out = strip_pii(record, "wg-gesucht", "iron")
    assert "posterName" not in out
    assert "onlineSince" not in out
    assert out["title"] == "Cosy"


def test_iron_kleinanzeigen_strips_nested_payload_pii() -> None:
    record = {
        "raw_payload": {
            "card": {"seller_name": "Hans", "title": "Helles 2-Zi"},
            "detail": {
                "seller": "Hans Müller",
                "sellerType": "Privater Nutzer Aktiv seit 2019",
                "sellerProfileHref": "/s-bestandsliste.html?userId=123",
                "embeddedStateSnippets": ["dataLayer = {...}"],
                "title": "detail title",
            },
            "scripts_or_state": ["window.__ = {...}"],
        }
    }
    out = strip_pii(record, "kleinanzeigen", "iron")
    rp = out["raw_payload"]
    assert "seller_name" not in rp["card"]
    assert rp["card"]["title"] == "Helles 2-Zi"
    for k in ("seller", "sellerType", "sellerProfileHref", "embeddedStateSnippets"):
        assert k not in rp["detail"]
    assert rp["detail"]["title"] == "detail title"
    assert "scripts_or_state" not in rp


# --- strip_pii: robustness --------------------------------------------------


def test_strip_pii_is_idempotent() -> None:
    record = {
        "dump": {
            "seller": {"name": "Hans", "type": "Privater Nutzer"},
            "embeddedState": [],
        }
    }
    once = strip_pii(copy.deepcopy(record), "kleinanzeigen", "bronze")
    twice = strip_pii(copy.deepcopy(once), "kleinanzeigen", "bronze")
    assert once == twice


def test_strip_pii_unknown_source_is_passthrough() -> None:
    record = {"dump": {"anything": 1}}
    assert strip_pii(copy.deepcopy(record), "wohninberlin", "bronze") == record
    assert strip_pii(copy.deepcopy(record), "kleinanzeigen", "weird-tier") == record


def test_strip_pii_handles_missing_and_none() -> None:
    # Missing dump / None values must not raise.
    assert strip_pii({}, "kleinanzeigen", "bronze") == {}
    assert strip_pii({"dump": None}, "wg-gesucht", "bronze") == {"dump": None}
    assert strip_pii({"dump": {"seller": None}}, "kleinanzeigen", "bronze") == {
        "dump": {"seller": None}
    }
    assert strip_pii(None, "kleinanzeigen", "bronze") is None  # type: ignore[arg-type]


# --- redact_freetext --------------------------------------------------------


def test_redact_email_and_phone() -> None:
    assert "[redacted]" in redact_freetext("Schreib mir: hans.m@example.de")
    assert "@example.de" not in redact_freetext("Schreib mir: hans.m@example.de")
    assert redact_freetext("Ruf 0151 2637 6735 an") == "Ruf [redacted] an"
    assert "[redacted]" in redact_freetext("Tel: +49 30 1234567")
    assert "[redacted]" in redact_freetext("WhatsApp: +49 151 2637 6735")
    assert "[redacted]" in redact_freetext("Festnetz 030 12345678 erreichbar")


def test_redact_preserves_non_contact_numbers() -> None:
    # Prices, areas, postal codes, years, room counts must survive intact.
    for text in (
        "Kaltmiete 1.200 € warm",
        "75 m² in 10115 Berlin",
        "Baujahr 2020, 3 Zimmer",
        "WBS erforderlich, 2. OG",
        "0,5 Zimmer Abstellraum",
    ):
        assert redact_freetext(text) == text


def test_redact_none_and_empty() -> None:
    assert redact_freetext(None) is None
    assert redact_freetext("") == ""
