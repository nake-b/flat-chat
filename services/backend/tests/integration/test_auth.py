"""HTTP integration tests for the fastapi-users auth flow.

  POST /api/auth/register  — create an account (hashed password)
  POST /api/auth/login     — set the session cookie (OAuth2 form: username+password)
  GET  /api/auth/me        — current user (cookie-authed)
  + protected app routes 401 without the cookie, 200 with it.

Mirrors test_conversations_api: one async engine + transaction rolled back on
exit. Both `get_async_db` (used by the auth routes) and the session store are
bound to that single connection via savepoints so every write — including the
registered user — disappears at the end. Gated on ``TEST_DATABASE_URL``.
"""

from __future__ import annotations

import asyncio

import httpx
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from flat_chat.chat.sessions import DbSessionStore
from flat_chat.core.database import get_async_db
from flat_chat.core.dependencies import get_session_store
from flat_chat.main import app

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
                        return await body(client)
                finally:
                    app.dependency_overrides.pop(get_async_db, None)
                    app.dependency_overrides.pop(get_session_store, None)
            finally:
                await trans.rollback()
    finally:
        await engine.dispose()


def drive(async_url, body):
    return asyncio.run(_run(async_url, body))


async def _register_and_login(client: httpx.AsyncClient) -> httpx.Response:
    reg = await client.post(
        "/api/auth/register", json={"email": EMAIL, "password": PASSWORD}
    )
    assert reg.status_code == 201, reg.text
    # fastapi-users login is an OAuth2 password form: username + password.
    return await client.post(
        "/api/auth/login", data={"username": EMAIL, "password": PASSWORD}
    )


def test_register_login_me_roundtrip(async_db_url):
    async def body(client):
        login = await _register_and_login(client)
        # Cookie transport → 204 No Content + Set-Cookie; the client jar keeps it.
        me = await client.get("/api/auth/me")
        return login, me

    login, me = drive(async_db_url, body)
    assert login.status_code == 204
    assert "flatchatauth" in login.headers.get("set-cookie", "")
    assert me.status_code == 200
    assert me.json()["email"] == EMAIL


def test_me_requires_cookie(async_db_url):
    async def body(client):
        return await client.get("/api/auth/me")

    resp = drive(async_db_url, body)
    assert resp.status_code == 401


def test_wrong_password_does_not_authenticate(async_db_url):
    async def body(client):
        await client.post(
            "/api/auth/register", json={"email": EMAIL, "password": PASSWORD}
        )
        bad = await client.post(
            "/api/auth/login", data={"username": EMAIL, "password": "wrong"}
        )
        me = await client.get("/api/auth/me")
        return bad, me

    bad, me = drive(async_db_url, body)
    assert bad.status_code == 400  # LOGIN_BAD_CREDENTIALS
    assert me.status_code == 401  # no cookie was set


def test_protected_app_route_gated_by_auth(async_db_url):
    """POST /api/conversations is 401 without auth, 200 once logged in."""

    async def body(client):
        anon = await client.post("/api/conversations")
        await _register_and_login(client)
        authed = await client.post("/api/conversations")
        return anon, authed

    anon, authed = drive(async_db_url, body)
    assert anon.status_code == 401
    assert authed.status_code == 200
    assert authed.json()["id"]
