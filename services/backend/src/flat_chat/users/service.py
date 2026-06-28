"""UserService — app-domain user policy, distinct from the auth lifecycle.

fastapi-users (`users/auth.py`) owns authentication: register / login / password
/ JWT. This service owns everything *about a user that isn't auth* and takes the
same `db: AsyncSession` constructor as `ListingService` / `SearchService`.

Today it's just `get(id)`. It is the deliberate home for the next things that are
per-user but not authentication:

  - **LLM rate-limiting / cost-control** — usage counters and a budget check the
    chat path consults before an agent run (e.g. `await user_service.check_budget(
    user_id)` / `record_usage(...)`). Keeping this here means adding it later is a
    method on an existing service, not a new cross-cutting concern.
  - profile reads, bookmarks ownership, etc.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from flat_chat.users.models import User


class UserService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, user_id: str) -> User | None:
        try:
            uid = UUID(user_id)
        except (ValueError, AttributeError, TypeError):
            return None
        return await self.db.get(User, uid)
