"""FastAPI application entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .auth import register_oidc
from .config import get_settings
from .routers import (
    annotations,
    attachments,
    auth,
    bib,
    collab,
    health,
    ingest,
    items,
    libraries,
    ml,
    notes,
    search,
)
from .storage import ensure_bucket

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
    yield


app = FastAPI(title="e4e Reference Manager API", version="0.1.0", lifespan=lifespan)

app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="lax")
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
app.include_router(attachments.router)
app.include_router(annotations.router)
app.include_router(collab.router)
app.include_router(notes.router)
app.include_router(ingest.router)
app.include_router(ml.router)
