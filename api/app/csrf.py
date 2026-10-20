"""CSRF defence for cookie-authenticated writes.

SameSite=lax stops cross-*site* requests from carrying the session cookie, but every
*.ucsd.edu host counts as the same site, so a page on any of them could still POST here
with the user's cookie. Unsafe requests that would be authenticated by the cookie (or by
dev-mode auth) must therefore prove they come from one of the app's own origins: the
`Origin` header, or failing that `Sec-Fetch-Site: same-origin` or a same-origin `Referer`.

Exempt: requests carrying an `Authorization` header (API tokens; a browser can't attach
one cross-origin without a CORS preflight we'd refuse), the MCP endpoint (token-only), and
the token-free one-time PDF upload links (`POST /uploads/{secret}`), whose secret in the
URL is the credential.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import Settings, get_settings

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
# (method, path) pairs that never use the session cookie; None matches any method.
_EXEMPT = (("POST", re.compile(r"/uploads/[^/]+/?")), (None, re.compile(r"/mcp(/.*)?")))
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(url: str) -> str | None:
    """`scheme://host[:port]` of a URL, lower-cased and without a default port."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in _DEFAULT_PORTS or not parts.hostname:
        return None
    host = parts.hostname.lower()
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    if port is not None and port != _DEFAULT_PORTS[parts.scheme]:
        host = f"{host}:{port}"
    return f"{parts.scheme}://{host}"


def allowed_origins(s: Settings) -> set[str]:
    """The web app's origin(s): where users land after login, the API's own public URL,
    and any CORS origins (the Vite dev server in local development)."""
    urls = [s.post_login_redirect, s.app_base_url, *s.cors_origins]
    return {o for o in map(_origin, urls) if o}


def _route_path(scope: Scope) -> str:
    path, root = scope.get("path", ""), scope.get("root_path", "")
    return path[len(root):] if root and path.startswith(root) else path


def _same_origin(headers: dict[str, str], allowed: set[str]) -> bool:
    origin = headers.get("origin")
    if origin is not None:
        return _origin(origin) in allowed  # "null" (sandboxed/opaque) never matches
    if headers.get("sec-fetch-site") == "same-origin":
        return True
    referer = headers.get("referer")
    return referer is not None and _origin(referer) in allowed


class CSRFMiddleware:
    def __init__(self, app: ASGIApp, session_cookie: str = "session") -> None:
        self.app = app
        self.cookie_prefix = f"{session_cookie}="

    def _needs_check(self, scope: Scope, headers: dict[str, str]) -> bool:
        method = scope["method"]
        if method in SAFE_METHODS or "authorization" in headers:
            return False
        path = _route_path(scope)
        if any(m in (None, method) and p.fullmatch(path) for m, p in _EXEMPT):
            return False
        cookies = headers.get("cookie", "").split(";")
        has_session = any(c.strip().startswith(self.cookie_prefix) for c in cookies)
        return has_session or get_settings().dev_auth

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers: dict[str, str] = {}
            for k, v in scope["headers"]:
                key, value = k.decode("latin-1").lower(), v.decode("latin-1")
                if key == "cookie" and key in headers:  # HTTP/2 may split cookies
                    value = f"{headers[key]}; {value}"
                headers[key] = value
            if self._needs_check(scope, headers) and not _same_origin(
                headers, allowed_origins(get_settings())
            ):
                response = JSONResponse(
                    {"detail": "Cross-origin request refused (CSRF check)"}, status_code=403
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
