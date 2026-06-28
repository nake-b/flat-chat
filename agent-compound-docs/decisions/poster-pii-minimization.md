# Poster-PII minimization — never store who posted a listing

**Status:** Implemented on `privacy/poster-pii-scrub` (June 2026). Loader
sanitizer + scraper trim + silver description redaction + one-off DB scrub +
unit/integration tests.

## Problem

We scrape apartment listings from Kleinanzeigen, Wg-gesucht, and HousingAnywhere
(plus wohninberlin). The listing *facts* are what the product needs — but the
scrapers were also collecting **private information about the original poster /
landlord / advertiser** and persisting it verbatim into the bronze + iron JSONB
blobs:

| Source | Where | Poster PII collected |
|---|---|---|
| Kleinanzeigen detail | `raw_listings.data.dump.seller` | name, "active since", **phone** |
| Kleinanzeigen detail | `raw_listings.data.dump.embeddedState` | 8000-char inline-`<script>` catch-all |
| Kleinanzeigen card+detail | `iron_cards.data.raw_payload.*` | `card.seller_name`, `detail.{seller, sellerType, sellerProfileHref, embeddedStateSnippets}`, `scripts_or_state` |
| Wg-gesucht detail | `raw_listings.data.dump.lister` | name, "member since", online-status, verified |
| Wg-gesucht card | `iron_cards.data` | `posterName`, `onlineSince` |
| HousingAnywhere detail | `raw_listings.data.dump.advertiser` | the entire advertiser object (name, photo, profile) |
| all (detail) | `listings.description` | poster phone/email/WhatsApp pasted into the body |

The typed/served tiers (`listings`, gold, platinum) were already clean — silver
only ever extracted a non-identifying **`lister_type`** category. The leak was in
the raw blobs and the free-text description.

## Decision

Defense-in-depth across three layers, sharing **one** PII spec
(`services/ingestion/src/pii.py`):

1. **Scrapers stop collecting** (minimization at source). Each
   `seller`/`lister`/`advertiser` block is reduced to its non-identifying
   `type`; the embedded-state blobs, profile URLs, and card-level poster fields
   are dropped. Wg-gesucht still reads the poster name *locally* to run its
   agency-name heuristic, but never returns it.

2. **Loaders stop persisting** — `strip_pii(record, source, tier)` runs in both
   `bronze/loader.py` and `iron/loader.py`, the single Python funnel every
   record (live *and* replayed) passes through before it becomes a DB row. This
   is the **tested contract**: it survives a future scraper regression or a new
   source author who forgets the rule.

3. **Silver redacts descriptions** — `redact_freetext()` (in
   `silver/sources/common.py`) strips phone/email/WhatsApp from the served
   `listings.description`. The raw body is kept in the internal-only bronze blob
   for debugging.

A one-off, idempotent **scrub script** (`scripts/scrub_poster_pii.py`) reuses
the same spec to clean rows that accumulated before the guards existed.

`lister_type` (private/agency/commercial) and wohninberlin's
`company_name`/`company_website` (commercial entities, not private individuals)
are **kept** — they are non-identifying product signal.

## Rejected

- **Allowlist (keep only known-good keys) instead of denylist.** Safer in
  principle, but silver reads a wide, evolving surface of `dump.*` / `entity.*`
  keys; an allowlist would silently drop new legitimate fields on every source
  change. We use a denylist of poster-identity key-paths and compensate with the
  description regex + the audit SQL below.
- **Sanitizing in the scraper `buildOutputRow` as the contract.** Per-scraper
  JS is untestable from the Python suite, and there are two Kleinanzeigen entry
  points that assemble records differently. The loaders are the one funnel.
- **A data-only Alembic migration for the scrub.** Would muddy the schema
  round-trip test (`test_alembic_round_trip.py`); the migration chain is for DDL.
  A standalone idempotent script is the right tool for data hygiene.

## "Adding a new scraper" checklist

When you add a source that exposes any poster field:
1. Make the scraper emit only the non-identifying `type` (never name/phone/
   profile/online-status/embedded-state).
2. Add the source's PII key-paths to `_STRIP_PATHS` in `pii.py` (per `tier`).
3. Pipe its `description` through `redact_freetext` in the silver transformer.
4. Extend the audit SQL + the integration fixture in `tests/integration/test_pii_scan.py`.

## Verification

- Unit: `tests/test_pii.py` (strip_pii per source/tier + redact_freetext
  false-positive corpus), `tests/test_silver_sources.py` (`lister_type` still
  derives from a `type`-only payload).
- Integration (gated on `TEST_DATABASE_URL`):
  `tests/integration/test_pii_scan.py` runs the real loaders, then asserts the
  audit SQL finds zero PII paths; a positive control confirms the audit detects
  unstripped data.
- DB audit: `jsonb_path_exists` / `jsonb_exists_any` queries over
  `raw_listings.data` / `iron_cards.data`, and a phone/email regex over
  `listings.description` — all must return zero after the scrub + a silver
  re-run.
