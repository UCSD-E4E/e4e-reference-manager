"""Pytest fixtures: a dedicated test Postgres DB (refman_test, or $REFMAN_TEST_DB), an
async ASGI client, and a
login() helper to drive endpoints as a chosen user (RBAC tests).

External services (translation-server, GROBID) are never hit; tests monkeypatch them.
"""
import asyncio
import os
import re

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import AuthContext, get_auth_context
from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models import Base, User

_URL = get_settings().database_url
_BASE = _URL.rsplit("/", 1)[0]
ADMIN_URL = _URL
# REFMAN_TEST_DB lets parallel runs (e.g. several worktrees) use separate databases;
# each run drops and recreates its own.
TEST_DB = os.environ.get("REFMAN_TEST_DB", "refman_test")
if not re.fullmatch(r"[a-z_][a-z0-9_]*", TEST_DB):
    raise ValueError(f"REFMAN_TEST_DB must be a plain identifier, got {TEST_DB!r}")
TEST_URL = f"{_BASE}/{TEST_DB}"

_ALL_TABLES = ", ".join(t.name for t in Base.metadata.sorted_tables)


@pytest.fixture(scope="session", autouse=True)
def _prepare_database():
    """Create a fresh test DB + schema once for the whole session (sync wrapper
    so it doesn't tangle with per-test event loops)."""

    async def setup():
        admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
        async with admin.connect() as conn:
            await conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)"))
            await conn.execute(text(f"CREATE DATABASE {TEST_DB}"))
        await admin.dispose()
        eng = create_async_engine(TEST_URL)
        async with eng.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
        await eng.dispose()

    asyncio.run(setup())
    yield


@pytest_asyncio.fixture
async def engine():
    """Per-test engine (binds to the test's event loop); truncates tables for isolation."""
    eng = create_async_engine(TEST_URL)
    async with eng.begin() as conn:
        await conn.execute(text(f"TRUNCATE {_ALL_TABLES} RESTART IDENTITY CASCADE"))
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session


@pytest.fixture
def _auth_state():
    return {"user": None}


@pytest_asyncio.fixture
async def client(engine, _auth_state):
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with Session() as session:
            yield session

    async def override_auth():
        if _auth_state["user"] is None:
            raise HTTPException(401, "no test user logged in")
        return AuthContext(_auth_state["user"])

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_auth_context] = override_auth
    transport = ASGITransport(app=app)
    # Like the web app's own fetches: writes carry the app's Origin (see app/csrf.py).
    origin = get_settings().post_login_redirect.rstrip("/")
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"Origin": origin}
    ) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def login(_auth_state):
    def _login(user: User):
        _auth_state["user"] = user

    return _login


@pytest_asyncio.fixture
async def make_user(db):
    async def _make(email: str, name: str = "User", org_role: str = "member") -> User:
        user = User(sub=f"test|{email}", email=email, name=name, org_role=org_role)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    return _make


@pytest_asyncio.fixture
async def alice(make_user, login):
    user = await make_user("alice@e4e.local", "Alice")
    login(user)
    return user


@pytest_asyncio.fixture
async def library(client, alice):
    """A user-owned library belonging to the logged-in alice."""
    r = await client.post("/libraries", json={"name": "Test Lib"})
    assert r.status_code == 201
    return r.json()["id"]
