"""Personal API tokens: a logged-in user mints a token in the web app, and scripts /
agents (e.g. an MCP server) send it as `Authorization: Bearer <token>` to act as that
user with the same library permissions. Only a hash is stored; the raw token is shown
once, at creation. Tokens can't mint or list other tokens (a leaked token can't
entrench itself)."""
from datetime import datetime, timedelta, timezone

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app import auth as auth_module
from app.auth import MAX_ACTIVE_TOKENS, get_auth_context, new_api_token
from app.config import Settings
from app.main import app
from app.models import ApiToken, Library


async def _mint(client, name="agent", **body):
    r = await client.post("/auth/tokens", json={"name": name, **body})
    assert r.status_code == 201, r.text
    return r.json()


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Managing tokens (as a logged-in browser user) ────────────────────────────────────


async def test_create_returns_secret_once(client, alice):
    tok = await _mint(client, "claude")
    assert tok["name"] == "claude"
    assert tok["token"].startswith("refman_")
    assert tok["token"].startswith(tok["prefix"])
    assert tok["expires_at"] is not None  # every token expires (default 90 days)
    assert tok["last_used_at"] is None

    r = await client.get("/auth/tokens")
    assert r.status_code == 200
    [listed] = r.json()
    assert listed["id"] == tok["id"]
    assert listed["prefix"] == tok["prefix"]
    assert "token" not in listed


async def test_tokens_are_unique(client, alice):
    a, b = await _mint(client, "a"), await _mint(client, "b")
    assert a["token"] != b["token"]


async def test_create_with_expiry(client, alice):
    tok = await _mint(client, expires_in_days=30)
    expires = datetime.fromisoformat(tok["expires_at"])
    expected = datetime.now(timezone.utc) + timedelta(days=30)
    assert abs((expires - expected).total_seconds()) < 60


@pytest.mark.parametrize(
    "body",
    [
        {"name": ""},
        {"name": "x", "expires_in_days": 0},
        {"name": "x", "expires_in_days": 366},
        {"name": "x", "expires_in_days": None},
    ],
)
async def test_create_validates(client, alice, body):
    r = await client.post("/auth/tokens", json=body)
    assert r.status_code == 422


async def test_default_expiry_is_90_days(client, alice):
    tok = await _mint(client)
    expires = datetime.fromisoformat(tok["expires_at"])
    expected = datetime.now(timezone.utc) + timedelta(days=90)
    assert abs((expires - expected).total_seconds()) < 60


async def test_only_hash_is_stored(client, alice, db):
    tok = await _mint(client)
    row = (await db.execute(select(ApiToken))).scalar_one()
    assert row.token_hash != tok["token"]
    assert tok["token"] not in row.token_hash


async def test_list_and_revoke_are_per_user(client, alice, make_user, login):
    tok = await _mint(client)
    bob = await make_user("bob@e4e.local", "Bob")
    login(bob)
    assert (await client.get("/auth/tokens")).json() == []
    assert (await client.delete(f"/auth/tokens/{tok['id']}")).status_code == 404

    login(alice)
    assert (await client.delete(f"/auth/tokens/{tok['id']}")).status_code == 204
    assert (await client.get("/auth/tokens")).json() == []


# ── Authenticating with a token (real auth, OIDC mode) ────────────────────


@pytest_asyncio.fixture
async def real_auth(client, monkeypatch):
    """Drop the test-user override so requests go through the real auth dependency."""
    monkeypatch.setattr(auth_module, "get_settings", lambda: Settings(dev_auth=False))
    app.dependency_overrides.pop(get_auth_context, None)
    return client


@pytest_asyncio.fixture
async def alice_token(db, alice):
    raw, row = new_api_token(alice, "agent", datetime.now(timezone.utc) + timedelta(days=30))
    db.add(row)
    await db.commit()
    return raw


async def test_bearer_token_authenticates_as_owner(real_auth, alice, alice_token):
    r = await real_auth.get("/auth/me", headers=_bearer(alice_token))
    assert r.status_code == 200
    assert r.json()["email"] == alice.email

    r = await real_auth.post("/libraries", json={"name": "Agent Lib"}, headers=_bearer(alice_token))
    assert r.status_code == 201
    library = r.json()["id"]
    r = await real_auth.get("/libraries", headers=_bearer(alice_token))
    assert r.status_code == 200
    assert [lib["id"] for lib in r.json()] == [library]


async def test_token_respects_library_permissions(real_auth, make_user, alice_token, db):
    """A token is exactly as privileged as its owner: no access to others' libraries."""
    bob = await make_user("bob@e4e.local", "Bob")
    lib = Library(name="Bob's", owner_id=bob.id)
    db.add(lib)
    await db.commit()
    r = await real_auth.get(f"/libraries/{lib.id}", headers=_bearer(alice_token))
    assert r.status_code in (403, 404)


async def test_scheme_is_case_insensitive(real_auth, alice_token):
    r = await real_auth.get("/auth/me", headers={"Authorization": f"bearer {alice_token}"})
    assert r.status_code == 200


@pytest.mark.parametrize(
    "value",
    [
        "Bearer refman_nope",
        "Bearer ",
        "Bearer",
        "Bearer\trefman_nope",
        "refman_nope",
        "Basic dXNlcjpwdw==",
    ],
)
async def test_bad_token_is_rejected(real_auth, value):
    r = await real_auth.get("/auth/me", headers={"Authorization": value})
    assert r.status_code == 401
    assert r.headers.get("www-authenticate", "").lower().startswith("bearer")


async def test_no_credentials_is_rejected(real_auth):
    assert (await real_auth.get("/auth/me")).status_code == 401


async def test_expired_token_is_rejected(real_auth, alice_token, db):
    row = (await db.execute(select(ApiToken))).scalar_one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db.commit()
    assert (await real_auth.get("/auth/me", headers=_bearer(alice_token))).status_code == 401


async def test_revoked_token_is_rejected(real_auth, alice_token, db):
    row = (await db.execute(select(ApiToken))).scalar_one()
    await db.delete(row)
    await db.commit()
    assert (await real_auth.get("/auth/me", headers=_bearer(alice_token))).status_code == 401


async def test_use_records_last_used(real_auth, alice_token, db):
    await real_auth.get("/auth/me", headers=_bearer(alice_token))
    row = (await db.execute(select(ApiToken))).scalar_one()
    await db.refresh(row)
    assert row.last_used_at is not None


async def test_last_used_is_only_rewritten_after_a_minute(real_auth, alice_token, db):
    row = (await db.execute(select(ApiToken))).scalar_one()
    recent = datetime.now(timezone.utc) - timedelta(seconds=10)
    row.last_used_at = recent
    await db.commit()
    await real_auth.get("/auth/me", headers=_bearer(alice_token))
    await db.refresh(row)
    assert row.last_used_at == recent

    stale = datetime.now(timezone.utc) - timedelta(minutes=2)
    row.last_used_at = stale
    await db.commit()
    await real_auth.get("/auth/me", headers=_bearer(alice_token))
    await db.refresh(row)
    assert row.last_used_at > stale


@pytest.mark.parametrize(
    "method,path",
    [("GET", "/auth/tokens"), ("POST", "/auth/tokens"), ("DELETE", f"/auth/tokens/{uuid.uuid4()}")],
)
async def test_token_cannot_manage_tokens(real_auth, alice_token, method, path):
    r = await real_auth.request(
        method, path, headers=_bearer(alice_token), json={"name": "sneaky"}
    )
    assert r.status_code == 403


async def test_token_wins_over_dev_auth(real_auth, monkeypatch, alice, alice_token):
    """In dev mode a token still identifies its owner (so agents can be tested locally),
    and a bad token is still refused rather than falling back to the dev user."""
    monkeypatch.setattr(auth_module, "get_settings", lambda: Settings(dev_auth=True))
    r = await real_auth.get("/auth/me", headers=_bearer(alice_token))
    assert r.json()["email"] == alice.email
    r = await real_auth.get("/auth/me", headers=_bearer("refman_nope"))
    assert r.status_code == 401


async def test_dev_auth_refuses_malformed_header(real_auth, monkeypatch):
    """A header that isn't `Bearer <token>` is an auth attempt gone wrong: refuse it
    instead of silently serving the request as the dev user."""
    monkeypatch.setattr(auth_module, "get_settings", lambda: Settings(dev_auth=True))
    r = await real_auth.get("/auth/me", headers={"Authorization": "refman_nope"})
    assert r.status_code == 401


async def test_openapi_declares_bearer_scheme(client):
    schema = (await client.get("/openapi.json")).json()
    schemes = schema["components"]["securitySchemes"].values()
    assert any(s["type"] == "http" and s["scheme"].lower() == "bearer" for s in schemes)


# ── Limits ─────────────────────────────────────────────────────────────────────────────


async def test_active_tokens_are_capped(client, alice, db):
    now = datetime.now(timezone.utc)
    for i in range(MAX_ACTIVE_TOKENS):
        db.add(new_api_token(alice, f"t{i}", now + timedelta(days=1))[1])
    db.add(new_api_token(alice, "old", now - timedelta(days=1))[1])  # expired: not counted
    await db.commit()
    r = await client.post("/auth/tokens", json={"name": "one too many"})
    assert r.status_code == 409


async def test_long_expired_tokens_are_pruned(client, alice, db):
    now = datetime.now(timezone.utc)
    db.add(new_api_token(alice, "ancient", now - timedelta(days=31))[1])
    db.add(new_api_token(alice, "recent", now - timedelta(days=1))[1])
    await db.commit()
    await _mint(client, "fresh")
    names = {t["name"] for t in (await client.get("/auth/tokens")).json()}
    assert names == {"recent", "fresh"}  # recently expired ones stay visible as expired
