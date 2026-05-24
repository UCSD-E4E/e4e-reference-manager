"""Groups, memberships, and library shares.

In production, group membership is typically synced from Authentik claims (see auth);
these endpoints let admins/members manage groups and sharing, and make RBAC testable
under dev-auth.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..db import get_session
from ..deps import library_manager, user_group_ids
from ..models import ACCESS_LEVELS, Group, Library, LibraryShare, User, user_group
from ..schemas import GroupCreate, GroupOut, MemberAdd, ShareIn, ShareOut, UserOut

router = APIRouter(tags=["collaboration"])


# --- Groups & membership ---


@router.post("/groups", response_model=GroupOut, status_code=201)
async def create_group(
    payload: GroupCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    exists = await session.scalar(select(Group).where(Group.slug == payload.slug))
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "Group slug already exists")
    group = Group(slug=payload.slug, name=payload.name, description=payload.description)
    session.add(group)
    await session.flush()
    # creator becomes a member
    await session.execute(insert(user_group).values(user_id=user.id, group_id=group.id))
    await session.commit()
    await session.refresh(group)
    return group


@router.get("/groups", response_model=list[GroupOut])
async def list_groups(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
):
    if user.org_role == "admin":
        rows = await session.execute(select(Group).order_by(Group.name))
        return list(rows.scalars().all())
    gids = await user_group_ids(session, user)
    if not gids:
        return []
    rows = await session.execute(select(Group).where(Group.id.in_(gids)).order_by(Group.name))
    return list(rows.scalars().all())


async def _require_group_access(group_id: uuid.UUID, user: User, session: AsyncSession) -> Group:
    group = await session.get(Group, group_id)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found")
    if user.org_role != "admin" and group_id not in await user_group_ids(session, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a member of this group")
    return group


@router.get("/groups/{group_id}/members", response_model=list[UserOut])
async def list_members(
    group_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await _require_group_access(group_id, user, session)
    rows = await session.execute(
        select(User).join(user_group, user_group.c.user_id == User.id).where(
            user_group.c.group_id == group_id
        )
    )
    return list(rows.scalars().all())


@router.post("/groups/{group_id}/members", response_model=list[UserOut], status_code=201)
async def add_member(
    group_id: uuid.UUID,
    payload: MemberAdd,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await _require_group_access(group_id, user, session)
    target = await session.scalar(select(User).where(User.email == payload.email))
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No user with that email (must have logged in)")
    already = await session.scalar(
        select(user_group.c.user_id).where(
            user_group.c.user_id == target.id, user_group.c.group_id == group_id
        )
    )
    if not already:
        await session.execute(insert(user_group).values(user_id=target.id, group_id=group_id))
        await session.commit()
    return await list_members(group_id, user, session)


@router.delete("/groups/{group_id}/members/{user_id}", status_code=204)
async def remove_member(
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await _require_group_access(group_id, user, session)
    await session.execute(
        delete(user_group).where(
            user_group.c.user_id == user_id, user_group.c.group_id == group_id
        )
    )
    await session.commit()


# --- Library shares (require manage on the library) ---


@router.get("/libraries/{library_id}/shares", response_model=list[ShareOut])
async def list_shares(
    lib: Library = Depends(library_manager), session: AsyncSession = Depends(get_session)
):
    rows = await session.execute(select(LibraryShare).where(LibraryShare.library_id == lib.id))
    return list(rows.scalars().all())


@router.post("/libraries/{library_id}/shares", response_model=ShareOut)
async def upsert_share(
    payload: ShareIn,
    lib: Library = Depends(library_manager),
    session: AsyncSession = Depends(get_session),
):
    if payload.access_level not in ACCESS_LEVELS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid access_level")
    if await session.get(Group, payload.group_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found")
    share = await session.scalar(
        select(LibraryShare).where(
            LibraryShare.library_id == lib.id, LibraryShare.group_id == payload.group_id
        )
    )
    if share is None:
        share = LibraryShare(
            library_id=lib.id, group_id=payload.group_id, access_level=payload.access_level
        )
        session.add(share)
    else:
        share.access_level = payload.access_level
    await session.commit()
    await session.refresh(share)
    return share


@router.delete("/libraries/{library_id}/shares/{group_id}", status_code=204)
async def delete_share(
    group_id: uuid.UUID,
    lib: Library = Depends(library_manager),
    session: AsyncSession = Depends(get_session),
):
    await session.execute(
        delete(LibraryShare).where(
            LibraryShare.library_id == lib.id, LibraryShare.group_id == group_id
        )
    )
    await session.commit()
