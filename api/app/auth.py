"""Authentication: Authentik OIDC with a dev-bypass mode for local development.

When REFMAN_DEV_AUTH=true (the default for local dev), OIDC is skipped and every request
is authenticated as a fixed dev user. In production set REFMAN_DEV_AUTH=false and provide
the OIDC_* settings; users then log in through Authentik.

Scripts and agents authenticate with a personal API token instead, sent as
`Authorization: Bearer refman_...`; it acts as the user who minted it.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from authlib.integrations.starlette_client import OAuth
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_session
from .models import ApiToken, Group, User, user_group

oauth = OAuth()

API_TOKEN_PREFIX = "refman_"
_LAST_USED_RESOLUTION = timedelta(minutes=1)
# Every token expires; a forgotten one stops working within a year at most.
DEFAULT_TOKEN_DAYS = 90
MAX_TOKEN_DAYS = 365
# Unexpired tokens a user may hold at once; expired ones are pruned after a grace period
# (they stay listed as "expired" meanwhile, so a 401 is easy to explain).
MAX_ACTIVE_TOKENS = 25
EXPIRED_TOKEN_RETENTION = timedelta(days=30)


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


def hash_api_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_api_token(user: User, name: str, expires_at: datetime) -> tuple[str, ApiToken]:
    """Mint a token for `user`. Returns the raw token (shown once) and the unsaved row."""
    raw = API_TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = ApiToken(
        user_id=user.id,
        name=name,
        prefix=raw[: len(API_TOKEN_PREFIX) + 6],
        token_hash=hash_api_token(raw),
        expires_at=expires_at,
    )
    return raw, row


@dataclass(frozen=True)
class AuthContext:
    """Who is calling, and how: `token_id` is set when they used an API token."""

    user: User
    token_id: uuid.UUID | None = None


# Declared as a dependency so the OpenAPI schema advertises Bearer auth. auto_error is
# off because a request may instead carry a session cookie (or nothing, in dev mode).
bearer_scheme = HTTPBearer(
    auto_error=False, description="Personal API token (`refman_...`) from the web app"
)


def _token_error(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"}
    )


async def _touch_token(session: AsyncSession, token_id: uuid.UUID, now: datetime) -> None:
    """Record use on a separate connection so auth never commits the request's session.
    The WHERE keeps it to one write per token per minute, even under concurrent bursts."""
    async with session.bind.begin() as conn:
        await conn.execute(
            update(ApiToken)
            .where(
                ApiToken.id == token_id,
                or_(
                    ApiToken.last_used_at.is_(None),
                    ApiToken.last_used_at < now - _LAST_USED_RESOLUTION,
                ),
            )
            .values(last_used_at=now)
        )


async def _context_from_token(session: AsyncSession, raw: str) -> AuthContext:
    now = datetime.now(timezone.utc)
    row = await session.scalar(
        select(ApiToken).where(ApiToken.token_hash == hash_api_token(raw))
    )
    if row is None or row.expires_at <= now:
        raise _token_error("Invalid or expired API token")
    if row.last_used_at is None or now - row.last_used_at > _LAST_USED_RESOLUTION:
        await _touch_token(session, row.id, now)
    return AuthContext(row.user, token_id=row.id)


async def get_auth_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> AuthContext:
    # A Bearer token always decides the identity, even in dev mode (so agents can be
    # exercised locally). Any other Authorization header is a botched attempt: refuse
    # it rather than fall back to the cookie or the dev user.
    if credentials is not None:
        return await _context_from_token(session, credentials.credentials.strip())
    if request.headers.get("authorization") is not None:
        raise _token_error("Malformed Authorization header; expected 'Bearer <token>'")
    s = get_settings()
    if s.dev_auth:
        user = await get_or_create_user(
            session, sub="dev|local", email=s.dev_user_email, name=s.dev_user_name
        )
        return AuthContext(user)
    sub = request.session.get("user_sub")
    if not sub:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    user = (await session.execute(select(User).where(User.sub == sub))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown user")
    return AuthContext(user)


async def get_current_user(ctx: AuthContext = Depends(get_auth_context)) -> User:
    return ctx.user


async def get_interactive_user(ctx: AuthContext = Depends(get_auth_context)) -> User:
    """A user signed in to the web app, not a script holding an API token. Guards token
    management, so a leaked token can't mint itself replacements."""
    if ctx.token_id is not None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "API tokens can't manage tokens; use the web app"
        )
    return ctx.user
