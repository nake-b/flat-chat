"""fastapi-users wiring — the auth lifecycle layer.

This module configures fastapi-users (register / login / logout / password / JWT)
and exposes the pieces the rest of the app needs:

  - `current_active_user` — the FastAPI dependency that resolves the logged-in
    `User` from the session cookie (401 when absent/invalid). `get_user_id()` in
    `core/dependencies.py` wraps it as the single identity seam.
  - `fastapi_users` — the instance whose `get_*_router()` factories `api/auth.py`
    wires into a router (mounted under `/api/auth` in `main.py`).
  - `UserManager` / schemas / `get_user_db` — used by `scripts/seed_users.py` to
    create the dev user with a properly hashed password.

This module owns only the auth lifecycle. App-domain user policy that isn't
authentication (profile reads, future per-user LLM rate-limiting / cost-control)
will get its own service when it's actually needed — it isn't built yet.

Transport is a **cookie** (httpOnly, SameSite=Lax) carrying a JWT signed with
`settings.jwt_secret`. Same-origin via nginx / the Vite proxy, so the browser
sends it automatically. `cookie_secure=False` because the MVP is served over
HTTP; flip it to True behind HTTPS. See AUTH.md.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

from fastapi import Depends
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin, schemas
from fastapi_users.authentication import (
    AuthenticationBackend,
    CookieTransport,
    JWTStrategy,
)
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from flat_chat.core.config import settings
from flat_chat.core.database import get_async_db
from flat_chat.users.models import User


# --- Pydantic schemas (wire shapes for the auth routes) --------------------
class UserRead(schemas.BaseUser[uuid.UUID]):
    pass


class UserCreate(schemas.BaseUserCreate):
    pass


class UserUpdate(schemas.BaseUserUpdate):
    pass


# --- Database adapter -------------------------------------------------------
async def get_user_db(
    session: AsyncSession = Depends(get_async_db),
) -> AsyncGenerator[SQLAlchemyUserDatabase]:
    yield SQLAlchemyUserDatabase(session, User)


# --- User manager (password hashing via pwdlib/argon2 by default) ----------
class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    # Secrets for the password-reset + email-verification token flows. We don't
    # expose those routes yet, but the manager requires them; reuse the JWT
    # secret rather than inventing more env vars.
    reset_password_token_secret = settings.jwt_secret
    verification_token_secret = settings.jwt_secret


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
) -> AsyncGenerator[UserManager]:
    yield UserManager(user_db)


# --- Authentication backend: cookie transport + JWT strategy ---------------
cookie_transport = CookieTransport(
    cookie_name="flatchatauth",
    cookie_max_age=settings.jwt_lifetime_seconds,
    cookie_httponly=True,
    cookie_samesite="lax",
    cookie_secure=settings.cookie_secure,  # False for local HTTP; True over HTTPS.
)


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(
        secret=settings.jwt_secret, lifetime_seconds=settings.jwt_lifetime_seconds
    )


auth_backend = AuthenticationBackend(
    name="cookie",
    transport=cookie_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])

# The single dependency the rest of the app consumes. 401s when there's no valid
# session cookie. `get_user_id()` wraps it.
current_active_user = fastapi_users.current_user(active=True)
