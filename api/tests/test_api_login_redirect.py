"""Login round-trip: a logged-out visitor is sent through Authentik and lands back on
the page they asked for (deep links, the PWA share target), never on another site."""
import pytest
from fastapi.responses import RedirectResponse

from app import auth as auth_module
from app.config import Settings
from app.routers import auth as auth_router

BASE = "https://bib.krg.ucsd.edu/"


class FakeAuthentik:
    async def authorize_redirect(self, request, redirect_uri):
        return RedirectResponse("https://auth.example/authorize")

    async def authorize_access_token(self, request):
        return {"userinfo": {"sub": "oidc|bob", "email": "bob@e4e.local", "name": "Bob"}}


@pytest.fixture
def oidc(monkeypatch):
    s = Settings(dev_auth=False, post_login_redirect=BASE)
    monkeypatch.setattr(auth_router, "get_settings", lambda: s)
    monkeypatch.setattr(auth_module, "get_settings", lambda: s)
    monkeypatch.setattr(auth_module.oauth, "authentik", FakeAuthentik(), raising=False)


async def _round_trip(client, params=None):
    r = await client.get("/auth/login", params=params)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "https://auth.example/authorize"
    r = await client.get("/auth/callback")
    assert r.status_code in (302, 307)
    return r.headers["location"]


async def test_login_returns_to_requested_page(client, oidc):
    loc = await _round_trip(client, {"next": "/share-target?url=https%3A%2F%2Fdoi.org%2F10.1%2Fx"})
    assert loc == "https://bib.krg.ucsd.edu/share-target?url=https%3A%2F%2Fdoi.org%2F10.1%2Fx"


async def test_login_without_next_goes_home(client, oidc):
    assert await _round_trip(client) == BASE


@pytest.mark.parametrize(
    "bad", ["//evil.example/x", "https://evil.example", "/\\evil.example", "items/1", ""]
)
async def test_login_ignores_offsite_next(client, oidc, bad):
    assert await _round_trip(client, {"next": bad}) == BASE


async def test_dev_auth_login_honours_next(client, monkeypatch):
    s = Settings(dev_auth=True, post_login_redirect=BASE)
    monkeypatch.setattr(auth_router, "get_settings", lambda: s)
    r = await client.get("/auth/login", params={"next": "/items/1"})
    assert r.headers["location"] == "https://bib.krg.ucsd.edu/items/1"
