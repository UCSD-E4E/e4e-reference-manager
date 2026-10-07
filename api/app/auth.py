"""Authentication: Authentik OIDC with a dev-bypass mode for local development.

When REFMAN_DEV_AUTH=true (the default for local dev), OIDC is skipped and every request
is authenticated as a fixed dev user. In production set REFMAN_DEV_AUTH=false and provide
the OIDC_* settings; users then log in through Authentik.
"""
from __future__ import annotations

import re

from authlib.integrations.starlette_client import OAuth
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_session
from .models import Group, User, user_group

oauth = OAuth()


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "group"


async def sync_user_groups(session: AsyncSession, user: User, group_names: list[str]) -> None:
    """Reconcile a user's group memberships from Authentik group claims (additive),
    and grant org admin if they're in the configured admin group."""
    s = get_settings()
    desired_ids = []
    # The claim can name a group more than once; one membership row per group.
    for name in dict.fromkeys(group_names):
        group = await session.scalar(select(Group).where(Group.authentik_ref == name))
        if group is None:
            base = _slugify(name)
            slug, i = base, 1
            while await session.scalar(select(Group).where(Group.slug == slug)) is not None:
                i += 1
                slug = f"{base}-{i}"
            group = Group(slug=slug, name=name, authentik_ref=name)
            session.add(group)
            await session.flush()
        desired_ids.append(group.id)

    existing = set(
        (
            await session.execute(
                select(user_group.c.group_id).where(user_group.c.user_id == user.id)
            )
        ).scalars()
    )
    for gid in desired_ids:
        if gid not in existing:
            await session.execute(insert(user_group).values(user_id=user.id, group_id=gid))

    if s.oidc_admin_group and s.oidc_admin_group in group_names:
        user.org_role = "admin"
    await session.commit()


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
        client_kwargs={"scope": "openid email profile groups"},
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
