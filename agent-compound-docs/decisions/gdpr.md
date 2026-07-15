# GDPR compliance — how FlatChat handles personal data

**Status:** Documented July 2026. Describes the data-protection posture for the
MVP; pairs with a user-facing privacy notice served at `/privacy.html`.

This is a compliance overview, not a decision record — but it follows the same
Problem / Decision / Rejected spine as the other docs here. It exists so a
reviewer (or examiner) can see, in one place, *what* personal data the system
touches and *why* that handling is lawful under the GDPR.

## Scope

FlatChat is a Berlin apartment-search chatbot. Two flows touch personal data,
and they are governed completely differently:

1. **Ingested listing data** (scraped from rental sites) — the person here is the
   *poster* (landlord / agent / advertiser). We deliberately store **none** of
   their identity.
2. **User-provided data** (the signed-in user chatting) — account email,
   conversation messages, and derived search state, including any location the
   user types to drive a proximity search. We store this, minimally, under a
   clear lawful basis.

## 1. Scraped listing data — no poster PII is ever stored

This is the strongest part of the posture, and it is *engineered and tested*, not
a policy promise.

The scrapers necessarily see poster-identifying fields in the source HTML/JSON
(seller/lister/advertiser name, phone, email, profile URL, "member since",
online-status, embedded-state script blobs). **None of it reaches the database.**
Defense-in-depth across three layers sharing one PII spec:

- **Scrapers emit only the non-identifying `type`** (private / agency /
  commercial) — minimization at source.
- **A mandatory loader choke-point** — `strip_pii(record, source, tier)` in
  `services/ingestion/src/pii.py` runs inside *both* `bronze/loader.py` and
  `iron/loader.py`, the single funnel every record (live and replayed) passes
  through before it becomes a DB row. It is a denylist of poster-identity
  key-paths per `(tier, source)`, idempotent and replay-safe. A future scraper
  regression or a new source author who forgets the rule is still caught here.
- **Free-text redaction** — `redact_freetext()` in
  `services/ingestion/src/silver/sources/common.py` strips phone / email /
  WhatsApp that posters paste into the served `listings.description`, while
  preserving prices, areas, postal codes, years, and room counts.

What the served `listings` table keeps is purely **property attributes** (title,
address *of the property*, rooms, area, rents, energy data, images, redacted
description) plus a non-identifying `lister_type`; and, **only for institutional
landlords** (e.g. municipal housing), a `company_name` / `company_website` —
these name a company, not a natural person.

This is verified end-to-end:

- Unit: `services/ingestion/tests/test_pii.py` (per-source/tier `strip_pii` +
  a `redact_freetext` false-positive corpus).
- Integration (gated on `TEST_DATABASE_URL`):
  `services/ingestion/tests/integration/test_pii_scan.py` runs the real loaders
  and asserts the audit SQL finds **zero** poster-PII rows; a positive control
  confirms the audit itself detects unstripped data.

Full record + the audit SQL: [`poster-pii-minimization.md`](poster-pii-minimization.md).

> **A property address is not, by itself, personal data of a natural person.** It
> becomes linkable to an individual only via the poster identity we deliberately
> discard — so the served listing set carries no natural-person PII.

## 2. User-provided data — stored, minimally, under contract necessity

The signed-in user's data is different: we *do* store it, because the product
cannot work otherwise.

**What we store:** account email (auth via fastapi-users), the conversation
transcript, and per-conversation `SessionState` (search filters, results, the
active listing) in the backend-owned `app.*` Postgres schema. When a user asks to
search near where they live/work, that location rides in the conversation and
in the derived search state.

**Lawful basis — contract necessity (Art. 6(1)(b)), not consent.** The user
asked us to perform a location-aware apartment search and to keep a usable
conversation history; we cannot deliver that service without processing the
location and retaining the thread. Necessity is the natural fit and avoids the
brittle "then I withdraw consent" dynamics of a consent basis. The data is used
*only* for the search + history the user requested (purpose limitation).

**Why full history retention is legitimate.** GDPR's storage-limitation principle
(Art. 5(1)(e)) does not require auto-expiry — it requires not keeping data longer
than necessary for the purpose. History *is* the feature; keeping it while the
account is active is necessary, disclosed in the privacy notice, and reversible by
the user at any time. (This is the same pattern consumer assistants like ChatGPT
and Claude follow: retain-while-active + user-controlled deletion, not a TTL.)

**Safeguards already in place:**
- **Per-user isolation** — every conversation route is ownership-checked via
  `get_user_id()`; a foreign conversation returns 404, never another user's data.
- **Right to erasure (Art. 17) is a database delete for us.** We never fine-tune
  a model on user data, so erasure does not require model retraining — it reduces
  to deleting rows. `DELETE /api/conversations/{id}` hard-deletes and cascades to
  messages + `session_state` via FK. (Art. 15 access is served by the existing
  history/state read endpoints.)
- **Transport to the LLM stays in the EU** — see below.

## 3. Processor / data residency (the LLM provider)

The LLM provider is a data processor: message text (which may contain the user's
location) crosses to it at inference time.

**Intended production deployment: Azure OpenAI, EU Data Zone (Standard).** This
confines generative-AI processing to EU member-state regions and keeps data at
rest in the selected Azure EU geography, and Azure OpenAI does not train on API
inputs by default. That gives EU residency + no-training for the one leg we don't
host ourselves.

**Honesty note (doc ↔ code):** the provider seam
(`services/backend/src/flat_chat/chat/providers/__init__.py`) is multi-provider
and selects on API-key presence, with a current preference order of
OpenAI → Anthropic → Azure. The Azure builder (`providers/azure.py`) already
wires Azure OpenAI, so the EU-Data-Zone deployment is *supported by the seam and
selected by configuration in production* — it is not hard-coded as the only
provider. If the production provider changes, update this section: the
residency/ no-training claim must always match the deployed configuration. All
three vendors offer no-training-by-default on their API/business tiers, but only
an EU-residency deployment satisfies the residency claim.

## What we deliberately do NOT do (and why)

Judged unnecessary for this MVP/thesis; each would add cost/complexity without
changing the compliance conclusion:

- **Redact the user's own location before storing it** — the location *is* the
  payload of a proximity search; redacting it would break the feature. Redaction
  is the right tool for *incidental* PII, which we don't solicit.
- **Retention TTL / auto-deletion job** — not required (retain-while-active +
  user delete satisfies storage limitation). Revisit if we ever hold data with no
  active-account purpose.
- **Coarse / derived-origin storage** (snapping the address to ~500 m) — a valid
  further-minimization step, deferred; contract necessity already covers storing
  the precise value the user provided.
- **Column-level / KMS encryption at rest** — beyond hosting-level at-rest
  encryption; overkill at this scale given per-user isolation + EU residency.
- **A consent checkbox gating sign-in** — wrong for a contract-necessity basis;
  we *inform* via the privacy notice rather than collect consent.

## Rejected alternatives

- **Consent as the lawful basis for chat history.** Rejected: it invites a
  withdrawal → mandatory-erasure workflow for data the service genuinely needs,
  and misrepresents a necessity as an optional extra.
- **Not persisting transcripts (ephemeral chat only).** Rejected: history/reload
  is a core UX requirement; the compliant path is to store-and-let-delete, not to
  drop the feature.

## Verification

- Scraping claim: `cd services/ingestion && uv run pytest tests/test_pii.py`
  (unit, no DB) and `tests/integration/test_pii_scan.py` (needs
  `TEST_DATABASE_URL`). Green ⇒ the "no poster PII stored" claim holds.
- Erasure: `DELETE /api/conversations/{id}` returns 204 and the conversation +
  its messages + state are gone (FK cascade).
- Privacy notice reachable: `GET /privacy.html` → 200 (linked from the login
  screen footer and the account menu).
