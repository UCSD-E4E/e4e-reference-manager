"""Access-control hardening: revoked Authentik access must not stick, sign-in errors and
races must not 500, cookie-authenticated writes need a same-origin request, and the app
refuses to start with dev settings in production."""
import asyncio

import pytest
import pytest_asyncio
from authlib.integrations.base_client.errors import MismatchingStateError, OAuthError
from fastapi.responses import RedirectResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import auth as auth_module
from app import csrf
from app.auth import get_auth_context, get_or_create_user, sync_user_groups
from app.config import Settings, check_settings
from app.main import app
from app.models import Group, User, user_group
from app.routers import auth as auth_router

SPA = "https://bib.krg.ucsd.edu/"
API = "https://bib.krg.ucsd.edu/api"


async def _group_names(db, user):
    rows = await db.scalars(
        select(Group.name)
        .join(user_group, user_group.c.group_id == Group.id)
        .where(user_group.c.user_id == user.id)
    )
    return set(rows)


def _use_settings(monkeypatch, **kw):
    s = Settings(**kw)
    for mod in (auth_module, auth_router, csrf):
        monkeypatch.setattr(mod, "get_settings", lambda: s)
    return s


# ── Group sync follows the claim both ways ───────────────────────────────────────────


async def test_relogin_drops_authentik_groups_missing_from_the_claim(db, make_user):
    user = await make_user("bob@e4e.local")
    await sync_user_groups(db, user, ["Lab A", "Lab B"])
    await sync_user_groups(db, user, ["Lab A"])
    assert await _group_names(db, user) == {"Lab A"}


async def test_relogin_keeps_manually_managed_groups(db, make_user):
    user = await make_user("bob@e4e.local")
    manual = Group(slug="reading-club", name="Reading club")  # no authentik_ref
    db.add(manual)
    await db.flush()
    await db.execute(user_group.insert().values(user_id=user.id, group_id=manual.id))
    await db.commit()

    await sync_user_groups(db, user, [])
    assert await _group_names(db, user) == {"Reading club"}


async def test_admin_role_follows_the_admin_group(db, make_user, monkeypatch):
    _use_settings(monkeypatch, oidc_admin_group="Admins")
    user = await make_user("bob@e4e.local")
    await sync_user_groups(db, user, ["Admins"])
    assert user.org_role == "admin"
    await sync_user_groups(db, user, ["Lab A"])
    assert user.org_role == "member"


async def test_admin_role_untouched_without_an_admin_group(db, make_user, monkeypatch):
    _use_settings(monkeypatch, oidc_admin_group="")
    user = await make_user("root@e4e.local", org_role="admin")
    await sync_user_groups(db, user, [])
    assert user.org_role == "admin"


# ── Concurrent first logins ──────────────────────────────────────────────────────────


async def test_concurrent_first_login_creates_one_user(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s1, Session() as s2:
        a, b = await asyncio.gather(
            get_or_create_user(s1, "oidc|new", "new@e4e.local", "New"),
            get_or_create_user(s2, "oidc|new", "new@e4e.local", "New"),
        )
    assert a.id == b.id
    async with Session() as s:
        assert await s.scalar(select(func.count()).select_from(User)) == 1


async def test_concurrent_logins_create_a_new_group_once(engine, make_user):
    u1, u2 = await make_user("a@e4e.local"), await make_user("b@e4e.local")
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s1, Session() as s2:
        await asyncio.gather(
            sync_user_groups(s1, await s1.merge(u1), ["Brand New"]),
            sync_user_groups(s2, await s2.merge(u2), ["Brand New"]),
        )
    async with Session() as s:
        assert await s.scalar(select(func.count()).select_from(Group)) == 1
        assert await s.scalar(select(func.count()).select_from(user_group)) == 2


# ── OIDC callback ────────────────────────────────────────────────────────────────────


class FakeAuthentik:
    def __init__(self, userinfo=None, error=None):
        self.userinfo = userinfo or {"sub": "oidc|bob", "email": "bob@e4e.local", "name": "Bob"}
        self.error = error

    async def authorize_redirect(self, request, redirect_uri):
        return RedirectResponse("https://auth.example/authorize")

    async def authorize_access_token(self, request):
        if self.error:
            raise self.error
        return {"userinfo": self.userinfo}


@pytest_asyncio.fixture
async def real_auth(client, monkeypatch):
    """OIDC mode through the real auth dependency (no test-user override)."""
    _use_settings(
        monkeypatch, dev_auth=False, post_login_redirect=SPA, app_base_url=API,
        oidc_admin_group="Admins",
    )
    app.dependency_overrides.pop(get_auth_context, None)
    return client


def _authentik(monkeypatch, **kw):
    fake = FakeAuthentik(**kw)
    monkeypatch.setattr(auth_module.oauth, "authentik", fake, raising=False)
    return fake


async def _sign_in(client):
    await client.get("/auth/login")
    r = await client.get("/auth/callback")
    assert r.status_code in (302, 307), r.text
    return r


@pytest.mark.parametrize("error", [MismatchingStateError(), OAuthError("access_denied")])
async def test_failed_callback_is_a_400_not_a_500(real_auth, monkeypatch, error):
    _authentik(monkeypatch, error=error)
    r = await real_auth.get("/auth/callback")
    assert r.status_code == 400
    assert "login" in r.text


async def test_callback_syncs_groups_and_role(real_auth, monkeypatch, db):
    fake = _authentik(monkeypatch)
    fake.userinfo = {**fake.userinfo, "groups": ["Admins", "Lab A"]}
    await _sign_in(real_auth)
    bob = await db.scalar(select(User).where(User.sub == "oidc|bob"))
    assert bob.org_role == "admin"
    assert await _group_names(db, bob) == {"Admins", "Lab A"}

    fake.userinfo = {**fake.userinfo, "groups": ["Lab A"]}
    await _sign_in(real_auth)
    await db.refresh(bob)
    assert bob.org_role == "member"
    assert await _group_names(db, bob) == {"Lab A"}


async def test_callback_without_a_groups_claim_leaves_memberships(real_auth, monkeypatch, db):
    # A missing claim means the scope/mapping is misconfigured, not "in no groups".
    fake = _authentik(monkeypatch)
    fake.userinfo = {**fake.userinfo, "groups": ["Lab A"]}
    await _sign_in(real_auth)
    fake.userinfo = {k: v for k, v in fake.userinfo.items() if k != "groups"}
    await _sign_in(real_auth)
    bob = await db.scalar(select(User).where(User.sub == "oidc|bob"))
    assert await _group_names(db, bob) == {"Lab A"}


# ── Absolute session lifetime ────────────────────────────────────────────────────────


async def test_session_works_within_its_lifetime(real_auth, monkeypatch):
    _authentik(monkeypatch)
    await _sign_in(real_auth)
    assert (await real_auth.get("/auth/me")).status_code == 200


async def test_session_expires_after_its_lifetime(real_auth, monkeypatch):
    _authentik(monkeypatch)
    await _sign_in(real_auth)
    real_now = auth_module.epoch_now()
    lifetime = Settings().session_lifetime_hours * 3600
    monkeypatch.setattr(auth_module, "epoch_now", lambda: real_now + lifetime + 60)
    assert (await real_auth.get("/auth/me")).status_code == 401


async def test_session_without_a_login_time_is_rejected(real_auth, monkeypatch):
    # Cookies minted before the lifetime existed carry no timestamp: sign in again.
    _authentik(monkeypatch)
    monkeypatch.setattr(auth_router, "login_timestamp", lambda: None)
    await _sign_in(real_auth)
    assert (await real_auth.get("/auth/me")).status_code == 401


# ── CSRF: cookie-authenticated writes must come from the app's own origin ────────────


@pytest_asyncio.fixture
async def browser(real_auth, monkeypatch):
    """A signed-in browser that sends no Origin unless a test sets one."""
    _authentik(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await _sign_in(c)
        yield c


async def _new_library(c, **headers):
    return await c.post("/libraries", json={"name": "L"}, headers=headers)


async def test_write_from_the_app_origin_is_allowed(browser):
    assert (await _new_library(browser, Origin="https://bib.krg.ucsd.edu")).status_code == 201


@pytest.mark.parametrize(
    "origin", ["https://evil.ucsd.edu", "https://bib.krg.ucsd.edu.evil.example", "null"]
)
async def test_cross_origin_cookie_write_is_refused(browser, origin):
    assert (await _new_library(browser, Origin=origin)).status_code == 403


async def test_write_with_no_origin_or_referer_is_refused(browser):
    assert (await _new_library(browser)).status_code == 403


async def test_same_origin_fetch_metadata_or_referer_is_enough(browser):
    assert (await _new_library(browser, **{"Sec-Fetch-Site": "same-origin"})).status_code == 201
    r = await _new_library(browser, Referer="https://bib.krg.ucsd.edu/libraries/x")
    assert r.status_code == 201
    r = await _new_library(browser, Referer="https://evil.ucsd.edu/")
    assert r.status_code == 403


async def test_reads_need_no_origin(browser):
    assert (await browser.get("/libraries")).status_code == 200


async def test_bearer_tokens_skip_the_origin_check(browser):
    r = await browser.post("/auth/tokens", json={"name": "agent"}, headers={"Origin": SPA[:-1]})
    token = r.json()["token"]
    r = await browser.post(
        "/libraries", json={"name": "L"},
        headers={"Authorization": f"Bearer {token}", "Origin": "https://evil.ucsd.edu"},
    )
    assert r.status_code == 201


async def test_one_time_upload_links_skip_the_origin_check(browser):
    # Token-free by design (the secret is the credential): 404 for an unknown link.
    r = await browser.post(
        "/uploads/not-a-real-secret",
        files={"file": ("p.pdf", b"%PDF-1.4", "application/pdf")},
        headers={"Origin": "https://evil.ucsd.edu"},
    )
    assert r.status_code == 404


def test_app_origins_come_from_settings():
    s = Settings(
        post_login_redirect="https://bib.krg.ucsd.edu/",
        app_base_url="https://api.example:8443/api",
        cors_origins=["http://localhost:5173"],
    )
    assert csrf.allowed_origins(s) == {
        "https://bib.krg.ucsd.edu",
        "https://api.example:8443",
        "http://localhost:5173",
    }


# ── Fail-closed configuration ────────────────────────────────────────────────────────


def test_local_dev_defaults_start():
    check_settings(Settings())


def test_production_settings_start():
    check_settings(
        Settings(app_base_url=API, dev_auth=False, session_secret="a-long-random-secret")
    )


def test_dev_auth_over_https_refuses_to_start():
    with pytest.raises(RuntimeError, match="dev_auth"):
        check_settings(Settings(app_base_url=API, dev_auth=True))


def test_default_session_secret_without_dev_auth_refuses_to_start():
    with pytest.raises(RuntimeError, match="session_secret"):
        check_settings(Settings(dev_auth=False))
