"""FastAPI application entrypoint."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from . import __version__
from .auth import register_oidc
from .config import Settings, check_settings, get_settings
from .csrf import CSRFMiddleware
from .db import SessionLocal
from .mcp_server import mcp_endpoint, mount_mcp
from .routers import (
    annotations,
    attachments,
    auth,
    auto_groups,
    bib,
    citations,
    collab,
    collections,
    health,
    ingest,
    items,
    libraries,
    ml,
    notes,
    search,
    trash,
    uploads,
    validate,
)
from .storage import ensure_bucket
from .trash import purge_trash_forever

logger = logging.getLogger("refman")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await run_in_threadpool(ensure_bucket)
    except Exception as exc:  # storage is optional for basic API use
        logger.warning("Could not ensure object-storage bucket: %s", exc)
    if register_oidc():
        logger.info("Authentik OIDC registered")
    elif not settings.dev_auth:
        logger.warning("OIDC not configured and dev_auth is off — logins will fail")
    # Purge references that have sat in a library's trash past the retention period.
    purger = asyncio.create_task(purge_trash_forever(SessionLocal))
    try:
        async with mcp_endpoint.running():
            yield
    finally:
        purger.cancel()


def session_cookie_https_only(s: Settings) -> bool:
    """Mark the session cookie Secure whenever the app is served over HTTPS."""
    return s.app_base_url.startswith("https://")


check_settings(settings)  # refuse to start with dev auth settings in a deployment

app = FastAPI(title="e4e Reference Manager API", version=__version__, lifespan=lifespan)

SESSION_COOKIE = "session"
# Middleware added later wraps earlier ones: CSRF runs inside CORS, so its 403s still
# carry CORS headers the dev web app can read.
app.add_middleware(CSRFMiddleware, session_cookie=SESSION_COOKIE)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie=SESSION_COOKIE,
    # The cookie lasts as long as a sign-in may (auth.py enforces it server-side too).
    max_age=settings.session_lifetime_hours * 3600,
    same_site="lax",
    https_only=session_cookie_https_only(settings),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(libraries.router)
app.include_router(items.router)
app.include_router(search.router)
app.include_router(bib.router)
app.include_router(collections.router)
app.include_router(auto_groups.router)
app.include_router(attachments.router)
app.include_router(annotations.router)
app.include_router(collab.router)
app.include_router(notes.router)
app.include_router(ingest.router)
app.include_router(ml.router)
app.include_router(validate.router)
app.include_router(trash.router)
app.include_router(uploads.router)
app.include_router(citations.router)
mount_mcp(app)
