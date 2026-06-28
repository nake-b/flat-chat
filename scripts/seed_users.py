#!/usr/bin/env python
"""Seed the application's accounts — the ONLY way users are created.

There is no public registration endpoint (see AUTH.md), so this script is how the
dev/admin and (optionally) a reviewer account come into existence. Idempotent:
each account is created with an Argon2-hashed password via the fastapi-users
`UserManager`, or skipped if its email already exists.

  - dev  — admin (superuser), from `DEV_USER_EMAIL` / `DEV_USER_PASSWORD`.
  - prof — regular user, created ONLY when both `PROF_USER_EMAIL` /
           `PROF_USER_PASSWORD` are set (e.g. the reviewer's login).

Standalone operational script (lives in `scripts/`, alongside `seed_listings.py`),
not a package module — but it depends on the backend package for the real auth
wiring (so we don't reimplement password hashing). Run it in the backend's env,
after `alembic upgrade head`, with the same env as the app (`DATABASE_URL`,
`JWT_SECRET`, the account vars):

    uv run --project services/backend python scripts/seed_users.py
"""

from __future__ import annotations

import asyncio

from fastapi_users.exceptions import UserAlreadyExists
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase

from flat_chat.core.config import settings
from flat_chat.core.database import AsyncSessionLocal
from flat_chat.users.auth import UserCreate, UserManager
from flat_chat.users.models import User


async def _create(
    manager: UserManager, email: str, password: str, *, is_superuser: bool
) -> None:
    try:
        user = await manager.create(
            UserCreate(
                email=email,
                password=password,
                is_superuser=is_superuser,
                is_verified=True,
            )
        )
        role = "admin" if is_superuser else "user"
        print(f"created {role} {user.email} ({user.id})")
    except UserAlreadyExists:
        print(f"{email} already exists — skipping")


async def seed_users() -> None:
    async with AsyncSessionLocal() as session:
        manager = UserManager(SQLAlchemyUserDatabase(session, User))
        await _create(
            manager,
            settings.dev_user_email,
            settings.dev_user_password,
            is_superuser=True,
        )
        if settings.prof_user_email and settings.prof_user_password:
            await _create(
                manager,
                settings.prof_user_email,
                settings.prof_user_password,
                is_superuser=False,
            )


def main() -> None:
    asyncio.run(seed_users())


if __name__ == "__main__":
    main()
