"""HTTP integration tests for the fastapi-users auth flow.

  POST /api/auth/login     — set the session cookie (OAuth2 form: username+password)
  GET  /api/auth/me        — current user (cookie-authed)
  + protected app routes 401 without the cookie, 200 with it.
  + public registration is CLOSED (no /api/auth/register route).

There is no public registration, so the login test creates its user the same way
production does — through the fastapi-users `UserManager` (Argon2-hashed), on the
test's own connection. Mirrors test_conversations_api: one async engine +
transaction rolled back on exit; both `get_async_db` (used by the auth routes) and
the session store are bound to that connection via savepoints. Gated on
``TEST_DATABASE_URL``.
"""

from __future__ import annotations

import asyncio

import httpx
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from flat_chat.chat.sessions import DbSessionStore
from flat_chat.core.database import get_async_db
from flat_chat.core.dependencies import get_session_store
from flat_chat.main import app
from flat_chat.users.auth import UserCreate, UserManager
from flat_chat.users.models import User

from ..conftest import DB_REQUIRED

pytestmark = DB_REQUIRED

EMAIL = "tester@flat-chat.dev"
PASSWORD = "s3cret-pw"


async def _run(async_url, body):
    engine = create_async_engine(async_url)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            try:
                factory = async_sessionmaker(
                    bind=conn,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                )

                async def _db_override():
                    async with factory() as session:
                        yield session

                app.dependency_overrides[get_async_db] = _db_override
                app.dependency_overrides[get_session_store] = lambda: DbSessionStore(
                    factory
                )
                try:
                    transport = ASGITransport(app=app)
                    async with httpx.AsyncClient(
                        transport=transport, base_url="http://test"
                    ) as client:
                        return await body(client, factory)
                finally:
                    app.dependency_overrides.pop(get_async_db, None)
                    app.dependency_overrides.pop(get_session_store, None)
            finally:
                await trans.rollback()
    finally:
        await engine.dispose()


def drive(async_url, body):
    return asyncio.run(_run(async_url, body))


async def _create_user(factory, email: str, password: str) -> None:
    """Provision a real, login-able user via the UserManager (Argon2 hash).

    This is the production path (no public registration), bound to the test's
    connection so it rolls back. Commits internally — visible to later requests on
    the same connection.
    """
    async with factory() as session:
        manager = UserManager(SQLAlchemyUserDatabase(session, User))
        await manager.create(UserCreate(email=email, password=password))


async def _login(client: httpx.AsyncClient, email: str, password: str):
    # fastapi-users login is an OAuth2 password form: username + password.
    return await client.post(
        "/api/auth/login", data={"username": email, "password": password}
    )


def test_login_me_roundtrip(async_db_url):
    async def body(client, factory):
        await _create_user(factory, EMAIL, PASSWORD)
        login = await _login(client, EMAIL, PASSWORD)
        me = await client.get("/api/auth/me")  # client jar carries the cookie
        return login, me

    login, me = drive(async_db_url, body)
    assert login.status_code == 204
    assert "flatchatauth" in login.headers.get("set-cookie", "")
    assert me.status_code == 200
    assert me.json()["email"] == EMAIL


def test_me_requires_cookie(async_db_url):
    async def body(client, factory):
        return await client.get("/api/auth/me")

    resp = drive(async_db_url, body)
    assert resp.status_code == 401


def test_wrong_password_does_not_authenticate(async_db_url):
    async def body(client, factory):
        await _create_user(factory, EMAIL, PASSWORD)
        bad = await _login(client, EMAIL, "wrong")
        me = await client.get("/api/auth/me")
        return bad, me

    bad, me = drive(async_db_url, body)
    assert bad.status_code == 400  # LOGIN_BAD_CREDENTIALS
    assert me.status_code == 401  # no cookie was set


def test_protected_app_route_gated_by_auth(async_db_url):
    """POST /api/conversations is 401 without auth, 200 once logged in."""

    async def body(client, factory):
        anon = await client.post("/api/conversations")
        await _create_user(factory, EMAIL, PASSWORD)
        await _login(client, EMAIL, PASSWORD)
        authed = await client.post("/api/conversations")
        return anon, authed

    anon, authed = drive(async_db_url, body)
    assert anon.status_code == 401
    assert authed.status_code == 200
    assert authed.json()["id"]


def test_public_registration_is_closed(async_db_url):
    """No register router is mounted — signup is seed-only (see AUTH.md)."""

    async def body(client, factory):
        return await client.post(
            "/api/auth/register", json={"email": EMAIL, "password": PASSWORD}
        )

    resp = drive(async_db_url, body)
    # No register route is mounted. 404 (no path) or 405 (the /{id} users route
    # claims the path for GET/PATCH/DELETE, so POST has no handler) — either way
    # there is no way to self-register.
    assert resp.status_code in (404, 405)
