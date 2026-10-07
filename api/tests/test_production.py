"""Production-deployment behaviour (bib.krg.ucsd.edu): OIDC scope + cookie hardening."""
from starlette.middleware.sessions import SessionMiddleware

from app import auth
from app.config import Settings, get_settings
from app.main import app, session_cookie_https_only


def test_register_oidc_requests_groups_scope(monkeypatch):
    # Authentik only emits the `groups` claim when the scope is requested; without it
    # group sync and oidc_admin_group always see [].
    captured = {}
    monkeypatch.setattr(
        auth, "get_settings",
        lambda: Settings(oidc_issuer="https://auth.example/app/", oidc_client_id="x"),
    )
    monkeypatch.setattr(auth.oauth, "register", lambda **kw: captured.update(kw))

    assert auth.register_oidc() is True
    assert "groups" in captured["client_kwargs"]["scope"].split()


def test_session_cookie_secure_when_served_over_https():
    assert session_cookie_https_only(Settings(app_base_url="https://bib.krg.ucsd.edu/api"))
    assert not session_cookie_https_only(Settings(app_base_url="http://localhost:8000"))


def test_session_middleware_uses_https_only_setting():
    mw = next(m for m in app.user_middleware if m.cls is SessionMiddleware)
    assert mw.kwargs["https_only"] == session_cookie_https_only(get_settings())
