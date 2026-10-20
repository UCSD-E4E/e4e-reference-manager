import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (
    EXPIRED_TOKEN_RETENTION,
    MAX_ACTIVE_TOKENS,
    get_current_user,
    get_interactive_user,
    get_or_create_user,
    new_api_token,
    oauth,
    sync_user_groups,
)
from ..config import get_settings
from ..db import get_session
from ..models import ApiToken, User
from ..schemas import ApiTokenCreate, ApiTokenCreated, ApiTokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _safe_next(next_path: str | None) -> str | None:
    """Accept only a same-site path ("/x"), never "//host" or "/\\host" (open redirect)."""
    if not next_path or not next_path.startswith("/") or next_path[1:2] in ("/", "\\"):
        return None
    return next_path


def _after_login_url(next_path: str | None) -> str:
    base = get_settings().post_login_redirect
    return base.rstrip("/") + next_path if next_path else base


@router.get("/login")
async def login(request: Request, next: str | None = None):
    s = get_settings()
    if s.dev_auth:
        return RedirectResponse(_after_login_url(_safe_next(next)))
    request.session["login_next"] = _safe_next(next)
    return await oauth.authentik.authorize_redirect(request, s.oidc_redirect_uri)


@router.get("/callback")
async def callback(request: Request, session: AsyncSession = Depends(get_session)):
    s = get_settings()
    token = await oauth.authentik.authorize_access_token(request)
    userinfo = token.get("userinfo") or {}
    sub = userinfo.get("sub")
    if not sub:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No subject in OIDC token")
    user = await get_or_create_user(
        session, sub=sub, email=userinfo.get("email", ""), name=userinfo.get("name", "")
    )
    groups = userinfo.get(s.oidc_groups_claim) or []
    if isinstance(groups, list):
        await sync_user_groups(session, user, [str(g) for g in groups])
    request.session["user_sub"] = sub
    return RedirectResponse(_after_login_url(_safe_next(request.session.pop("login_next", None))))


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return {"status": "ok"}


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user


@router.get("/tokens", response_model=list[ApiTokenOut])
async def list_tokens(
    user: User = Depends(get_interactive_user), session: AsyncSession = Depends(get_session)
):
    rows = await session.scalars(
        select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.created_at)
    )
    return list(rows)


@router.post("/tokens", response_model=ApiTokenCreated, status_code=status.HTTP_201_CREATED)
async def create_token(
    body: ApiTokenCreate,
    user: User = Depends(get_interactive_user),
    session: AsyncSession = Depends(get_session),
):
    now = datetime.now(timezone.utc)
    await session.execute(
        delete(ApiToken).where(
            ApiToken.user_id == user.id,
            ApiToken.expires_at < now - EXPIRED_TOKEN_RETENTION,
        )
    )
    active = await session.scalar(
        select(func.count())
        .select_from(ApiToken)
        .where(ApiToken.user_id == user.id, ApiToken.expires_at > now)
    )
    if active >= MAX_ACTIVE_TOKENS:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"You already have {MAX_ACTIVE_TOKENS} active tokens; revoke one first",
        )
    raw, row = new_api_token(user, body.name, now + timedelta(days=body.expires_in_days))
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return ApiTokenCreated(**ApiTokenOut.model_validate(row).model_dump(), token=raw)


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    token_id: uuid.UUID,
    user: User = Depends(get_interactive_user),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(ApiToken, token_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Token not found")
    await session.delete(row)
    await session.commit()
