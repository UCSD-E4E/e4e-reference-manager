from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user, get_or_create_user, oauth, sync_user_groups
from ..config import get_settings
from ..db import get_session
from ..models import User
from ..schemas import UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
async def login(request: Request):
    s = get_settings()
    if s.dev_auth:
        return RedirectResponse(s.post_login_redirect)
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
    return RedirectResponse(s.post_login_redirect)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return {"status": "ok"}


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user
