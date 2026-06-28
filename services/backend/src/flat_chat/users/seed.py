"""Seed the dev user — `python -m flat_chat.users.seed`.

Idempotent: creates a real, login-able user from `DEV_USER_EMAIL` /
`DEV_USER_PASSWORD` (password Argon2-hashed via the fastapi-users UserManager),
or no-ops if that email already exists. This is the login handed to reviewers; it
is NOT a schema migration (migrations stay pure-schema).

Run it after `alembic upgrade head`. Requires the same env as the app
(`DATABASE_URL`, `JWT_SECRET`, the `DEV_USER_*` pair).
"""

from __future__ import annotations

import asyncio

from fastapi_users.exceptions import UserAlreadyExists
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase

from flat_chat.core.config import settings
from flat_chat.core.database import AsyncSessionLocal
from flat_chat.users.auth import UserCreate, UserManager
from flat_chat.users.models import User


async def seed_dev_user() -> None:
    async with AsyncSessionLocal() as session:
        manager = UserManager(SQLAlchemyUserDatabase(session, User))
        try:
            user = await manager.create(
                UserCreate(
                    email=settings.dev_user_email,
                    password=settings.dev_user_password,
                    is_superuser=True,
                    is_verified=True,
                )
            )
            print(f"created dev user {user.email} ({user.id})")
        except UserAlreadyExists:
            print(f"dev user {settings.dev_user_email} already exists — skipping")


def main() -> None:
    asyncio.run(seed_dev_user())


if __name__ == "__main__":
    main()
