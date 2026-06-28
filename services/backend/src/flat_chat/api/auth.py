"""Auth routes (fastapi-users) — mounted under `/api/auth` in `main.py`.

Combines the login/logout cookie router and the user routes (`/me`) behind one
`APIRouter` so `main.py` mounts auth exactly the way it mounts chat / agent /
listings (import a `router`, include it once). The route handlers themselves are
factory-produced by fastapi-users; this module is the thin home that gives the
auth surface an obvious place to live.

There is deliberately NO register router — accounts are seed-only
(`scripts/seed_users.py`; see AUTH.md). Add `fastapi_users.get_register_router`
here (ideally gated) if self-service signup is ever wanted.
"""

from fastapi import APIRouter

from flat_chat.users.auth import UserRead, UserUpdate, auth_backend, fastapi_users

router = APIRouter()
router.include_router(fastapi_users.get_auth_router(auth_backend))
router.include_router(fastapi_users.get_users_router(UserRead, UserUpdate))
