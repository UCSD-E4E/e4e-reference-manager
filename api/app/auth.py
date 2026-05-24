"""Authentication: Authentik OIDC with a dev-bypass mode for local development.

When REFMAN_DEV_AUTH=true (the default for local dev), OIDC is skipped and every request
is authenticated as a fixed dev user. In production set REFMAN_DEV_AUTH=false and provide
the OIDC_* settings; users then log in through Authentik.
"""
from __future__ import annotations

from authlib.integrations.starlette_client import OAuth
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_session
from .models import User

oauth = OAuth()


def register_oidc() -> bool:
    """Register the Authentik OIDC client if configured. Returns True if registered."""
    s = get_settings()
    if not (s.oidc_issuer and s.oidc_client_id):
        return False
    oauth.register(
        name="authentik",
        client_id=s.oidc_client_id,
        client_secret=s.oidc_client_secret,
        server_metadata_url=s.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
    return True


async def get_or_create_user(session: AsyncSession, sub: str, email: str, name: str) -> User:
    user = (await session.execute(select(User).where(User.sub == sub))).scalar_one_or_none()
    if user is None:
        user = User(sub=sub, email=email, name=name)
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


async def get_current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User:
    s = get_settings()
    if s.dev_auth:
        return await get_or_create_user(
            session, sub="dev|local", email=s.dev_user_email, name=s.dev_user_name
        )
    sub = request.session.get("user_sub")
    if not sub:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    user = (await session.execute(select(User).where(User.sub == sub))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown user")
    return user
