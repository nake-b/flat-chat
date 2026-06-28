"""Poster-PII sanitizer for the iron + bronze tiers.

Every record that lands in ``iron_cards.data`` or ``raw_listings.data`` passes
through :func:`strip_pii` in the loaders, so identifying information about the
original poster / landlord / advertiser never reaches the database — even if a
scraper (or a future source) emits it. The same spec backs the one-off
``scripts.scrub_poster_pii`` cleanup of already-stored rows, so there is a
single source of truth for "what counts as poster PII".

Only the non-identifying lister *category* (``type`` → silver's ``lister_type``)
is retained; everything that names, locates, or time-fingerprints the poster is
dropped: names, phone numbers, profile URLs/handles, "member/active since"
dates, online-status, and the unbounded embedded-state script blobs that can
carry session/user data.

Pure module — no DB, no IO — so it is trivially unit-testable.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

# Per-(tier, source) dotted key-paths to delete when present. A path's final
# segment is the key removed from the dict reached by walking the earlier
# segments; any missing/None step is a no-op (idempotent, replay-safe).
#
# `type` is deliberately NOT stripped anywhere — it is the benign
# private/agency/commercial category consumed by `map_lister_type`.
_STRIP_PATHS: dict[tuple[str, str], tuple[str, ...]] = {
    # ---- bronze (raw_listings.data — detail payload under `dump`) ----------
    ("bronze", "kleinanzeigen"): (
        "dump.seller.name",
        "dump.seller.phone",
        "dump.seller.activeSince",
        "dump.embeddedState",
    ),
    ("bronze", "wg-gesucht"): (
        "dump.lister.name",
        "dump.lister.memberSince",
        "dump.lister.online",
        "dump.lister.verified",
    ),
    ("bronze", "housinganywhere"): (
        # `dump.advertiser` is reduced to {type} separately (see strip_pii);
        # these defend against poster identity nested in the listing entity.
        "dump.entity.advertiser",
        "dump.entity.user",
        "dump.entity.owner",
        "dump.entity.host",
    ),
    # ---- iron (iron_cards.data — card tier, shape differs per source) ------
    ("iron", "wg-gesucht"): (
        "posterName",
        "onlineSince",
    ),
    ("iron", "kleinanzeigen"): (
        "raw_payload.card.seller_name",
        "raw_payload.detail.seller",
        "raw_payload.detail.sellerType",
        "raw_payload.detail.sellerProfileHref",
        "raw_payload.detail.embeddedStateSnippets",
        "raw_payload.scripts_or_state",
    ),
}


def _delete_path(obj: Any, keys: Sequence[str]) -> None:
    """Delete ``keys[-1]`` from the nested dict reached via ``keys[:-1]``.

    No-op if any intermediate step is missing or not a dict.
    """
    for key in keys[:-1]:
        if not isinstance(obj, dict):
            return
        obj = obj.get(key)
    if isinstance(obj, dict):
        obj.pop(keys[-1], None)


def _reduce_advertiser_to_type(record: dict) -> None:
    """Collapse HousingAnywhere's full advertiser object to just ``{type}``."""
    dump = record.get("dump")
    if not isinstance(dump, dict):
        return
    adv = dump.get("advertiser")
    if isinstance(adv, dict):
        dump["advertiser"] = {"type": adv.get("type")}


def strip_pii(record: dict, source: str, tier: str) -> dict:
    """Remove poster-identifying fields from ``record`` in place and return it.

    Idempotent (delete-if-present) and safe on missing keys / ``None`` values.
    ``tier`` is ``"bronze"`` (raw_listings) or ``"iron"`` (iron_cards); ``source``
    is the scraper's ``source_name``. Unknown ``(tier, source)`` pairs pass
    through unchanged, so a source with no known PII is simply a no-op.
    """
    if not isinstance(record, dict):
        return record
    if tier == "bronze" and source == "housinganywhere":
        _reduce_advertiser_to_type(record)
    for path in _STRIP_PATHS.get((tier, source), ()):
        _delete_path(record, path.split("."))
    return record
