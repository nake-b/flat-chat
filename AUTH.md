# Authentication — what we run, why, and where it grows

Real password authentication via **[fastapi-users](https://fastapi-users.github.io/fastapi-users/)**.
A seeded dev user logs in with email + password; the session is a signed,
httpOnly JWT cookie. This file records what exists, the choice behind it, and the
documented path to a hosted IdP (Logto) later.

Companion decision doc: [`agent-compound-docs/decisions/session-persistence.md`](agent-compound-docs/decisions/session-persistence.md).

## What runs today

- **Library:** fastapi-users 15.x (`fastapi-users[sqlalchemy]`). Passwords are
  hashed with **Argon2 via `pwdlib`** (the maintained replacement for `passlib`,
  which is broken on Python 3.13+). Wired in `services/backend/src/flat_chat/users/auth.py`.
- **Transport:** a single httpOnly, SameSite=Lax **cookie** carrying a JWT signed
  with `JWT_SECRET`. Same-origin (nginx / Vite proxy), so the browser sends it
  automatically. `cookie_secure=False` for the HTTP MVP — flip to True behind HTTPS.
- **Routes** (all under `/api/auth`, mounted in `main.py`): `register`, `login`,
  `logout`, and the user routes (`/me`). Nginx proxies `/api/auth`.
- **The identity seam is unchanged in shape.** `get_user_id()` in
  `core/dependencies.py` still returns a `str` user id and every route still
  depends on `Depends(get_user_id)` — but it now resolves the authenticated
  fastapi-users `current_active_user` from the cookie (401 when absent) instead of
  returning a constant. One function changed; no call sites did.
- **Dev login.** `python -m flat_chat.users.seed` idempotently creates a real,
  login-able user from `DEV_USER_EMAIL` / `DEV_USER_PASSWORD` (defaults
  `dev@flat-chat.dev` / `dev`). This is the credential handed to reviewers. It is
  a dev script, **not** a migration (migrations stay pure-schema).
- **Frontend.** A `LoginGate` wraps the app: it checks the session once, shows a
  login form when anonymous, and only mounts the conversation bring-up once
  authenticated. Sign-out lives in the chat header. See
  `services/frontend/src/{components/LoginGate.tsx,hooks/useAuth.ts,api/auth.ts}`.

### App-domain user policy vs auth lifecycle

Two layers, kept separate on purpose:

- **fastapi-users (`users/auth.py`)** owns the *auth lifecycle* — register /
  login / logout / password / JWT.
- **`users/service.py:UserService(db)`** owns *app-domain user policy* — `get(id)`
  today, and the deliberate home for the next per-user-but-not-auth concern:
  **LLM rate-limiting / cost-control** (usage counters + a budget check the chat
  path consults before an agent run). Adding it later is a method on an existing
  service, not a new cross-cutting concern.

### The user model

`app.users` carries the fastapi-users contract columns (`email`,
`hashed_password`, `is_active` / `is_superuser` / `is_verified`) added by migration
`0002_user_auth_columns`. `User` is defined by hand in `users/models.py` (the
SQLAlchemy adapter only reads attributes, so inheriting the library mixin isn't
required) so the primary key keeps its `gen_random_uuid()` server default from
`0001` — no conversation foreign keys are re-keyed.

`email` / `hashed_password` are **NOT NULL**: every user is a real account.
There are no dummy / placeholder rows — `DbSessionStore.create` no longer
fabricates a user, so a conversation can only reference a user that already exists
(registration or `users.seed`). Because the columns are NOT NULL with no default,
migration `0002` must run against an **empty `app.users` table** (a fresh /
refreshed dev DB) — a deliberate dev-only stance, since the only accounts are a
seeded dev user and the reviewer's. `DUMMY_USER_ID` survives only as a fixed id for
`InMemorySessionStore` unit tests.

## Why fastapi-users (and not Logto, yet)

An earlier plan named self-hosted **Logto** as preferred. We reversed that for
the MVP: fastapi-users is **library-only — nothing new to deploy**, which matters
for a single-process app handed to a reviewer. Priorities held: EU/GDPR-safe
(all user data in our own Postgres), OSS, FastAPI + React friendly, Python 3.14.

**Logto remains the documented migration path.** It earns its keep once we want
social / OIDC login, multiple apps sharing one identity, or to stop owning the
auth surface. Because the whole app reads identity through `get_user_id()`, that
migration is: stand up Logto, validate its JWT/session in `get_user_id()` (or a
small dependency it calls), keep the `app.users` row keyed by the Logto subject.
Conversations and bookmarks keep their foreign keys.

**Authlib (social login) is deferred.** It's the OAuth/OIDC *client* piece — not
needed for password auth, and it manages no passwords. The extension point: add
an OAuth account table + `fastapi_users.get_oauth_router(...)` when social login
is wanted, or migrate straight to Logto.

We also **skipped the anonymous-cookie stage** the old plan listed between dummy
and real auth — there was no anonymous data worth preserving for a fresh,
reviewer-facing deployment, so we went straight to real accounts.

### Explicitly rejected

- **Clerk / Firebase Auth** — managed, but **US-resident user data** → GDPR
  problem for an EU product.
- **`passlib`** — unmaintained, broken on Python 3.13+. fastapi-users already uses
  `pwdlib` (Argon2), so this is handled for us.

## The ownership gap is closed

`POST /api/agent` used to resolve a conversation straight from the AG-UI
envelope's `thread_id` with no owner check — fine under a single dummy user,
unsafe the moment `get_user_id()` returns a real user. It now mirrors the REST
reads: `ChatService.dispatch_agent_request` takes the authenticated `user_id` and
gates `session.user_id == user_id`, raising `SessionNotFoundError` → **404 (not
403)** on a mismatch so existence doesn't leak. Covered by
`test_dispatch_history.py::test_foreign_session_is_rejected_before_run` and the
REST equivalent `test_conversations_api.py::test_foreign_conversation_is_404_not_403`.

## Operational notes

- `JWT_SECRET` is **required** (no insecure default ships). Generate with
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Rotating it logs
  everyone out. Tests set a sentinel in `conftest.py`.
- Bring-up order: **start from a fresh/empty `app.users`** → `alembic upgrade head`
  → `python -m flat_chat.users.seed` → run. Migration `0002` adds NOT-NULL columns
  with no default, so a DB still holding pre-auth rows must be cleared first
  (`./scripts/refresh-db.sh`, or drop/recreate the DB, or `DELETE FROM app.users`
  which cascades to its conversations). No production data exists to preserve.
