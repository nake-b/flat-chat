# Authentication — current state, the plan, and the open TODO

There is **no authentication yet**. Every request runs as a single hardcoded
dummy user. This file records what exists today, how it is designed to grow into
real auth without re-keying data, the simple/free options we'd reach for when we
get there, and the one ownership gap that real auth must close.

Companion decision doc: [`agent-compound-docs/decisions/session-persistence.md`](agent-compound-docs/decisions/session-persistence.md)
(the auth/storage research is folded in there).

## Where we are today (stage 1 — dummy user)

- **One seam:** `get_user_id()` in `services/backend/src/flat_chat/core/dependencies.py`
  returns a fixed UUID (`DUMMY_USER_ID`, `users/models.py`). Every route that
  needs identity depends on `Depends(get_user_id)`, so **only this function
  changes** as auth evolves — call sites never do.
- **The user row is materialized on demand** — `DbSessionStore.create` does an
  `INSERT … ON CONFLICT DO NOTHING`, so there's no seed migration and the
  migration stays pure-schema.
- **Conversations are scoped to the user.** `app.conversations.user_id` →
  `app.users.id`. The REST reads (`GET /api/conversations/{id}/messages`,
  `/state`) are ownership-checked via `_load_owned` and return **404, not 403**,
  for a foreign conversation so existence doesn't leak.

## The growth path (claim-in-place)

The `users` row is deliberately minimal (`id` + timestamps) and designed to be
**claimed in place**: when real accounts land, add nullable `email` /
`password_hash` / `auth_provider` / `claimed_at` columns and `UPDATE` the
existing row on signup. The **primary key never changes**, so conversations
(and future bookmarks) keep their foreign keys without re-keying.

Three stages, each swapping only `get_user_id()`:

| Stage | `get_user_id()` returns | Notes |
|-------|-------------------------|-------|
| 1 (now) | a fixed dummy UUID | no auth, single shared user |
| 2 | a per-browser **anonymous** id from a signed cookie | upsert a fresh `users` row per browser; conversations become genuinely per-visitor with zero login friction |
| 3 | the authenticated user's id (e.g. JWT `sub`) | real accounts; the stage-2 anonymous row is *claimed* (UPDATE, not insert) on signup so prior conversations survive |

## Simple / free ways to do real auth (stage 3)

Priorities for this project: **EU/GDPR-safe data residency**, OSS or
self-hostable, and Python 3.14 / FastAPI + React friendly.

- **Self-hosted [Logto](https://logto.io/)** — *preferred.* Lightest OSS
  identity provider with official FastAPI + React SDKs; self-host keeps all
  user data in our own Postgres/region. Handles email+password, social, and
  OIDC. One more compose service.
- **[`fastapi-users`](https://fastapi-users.github.io/fastapi-users/) + [Authlib](https://authlib.org/)** —
  *no extra service.* Library-only: user models, registration, login, JWT, and
  OAuth flows live in our backend. More wiring than Logto but nothing new to
  deploy; good if we want to stay single-process.
- **Cookie-only anonymous (stage 2)** — not "auth" per se, but the cheapest
  real win: a signed, HTTP-only cookie carrying an anonymous user id. No
  passwords, no third party, no PII. This is the recommended first increment.

### Explicitly rejected

- **Clerk / Firebase Auth** — managed convenience, but **US-resident user
  data** → GDPR problem for an EU product. Disqualified.
- **`passlib`** — effectively unmaintained and **broken on Python 3.13+**. If a
  library needs password hashing, use **[`pwdlib`](https://pypi.org/project/pwdlib/)**
  (argon2/bcrypt) instead.

## Open TODO — ownership check on `POST /api/agent`

The read endpoints are ownership-checked; **the agent endpoint is not.**
`ChatService.dispatch_agent_request` resolves the conversation straight from the
AG-UI envelope's `thread_id` and never compares it to the request user. Today
that's harmless (one dummy user), but the moment `get_user_id()` returns a real
user, **any caller who knows a `thread_id` could continue or read another user's
conversation through the agent.**

- **Marker:** `TODO(auth)` at the `store.get(session_id)` call in
  `services/backend/src/flat_chat/chat/service.py`.
- **Fix when auth lands:** thread `get_user_id()` into `dispatch_agent_request`
  and gate the resolved session on `session.user_id == request_user`, 404-ing a
  mismatch — the same contract `_load_owned` already enforces for the REST
  routes. Add a test mirroring `test_foreign_conversation_is_404_not_403`.

Until then this is a known, accepted limit of the single-user MVP.
