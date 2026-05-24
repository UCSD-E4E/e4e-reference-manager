from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..db import get_session
from ..deps import library_access_level, library_viewer, user_group_ids
from ..models import AuditEvent, Library, LibraryShare, User
from ..schemas import AuditEventOut, LibraryCreate, LibraryOut

router = APIRouter(prefix="/libraries", tags=["libraries"])


@router.get("", response_model=list[LibraryOut])
async def list_libraries(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
):
    if user.org_role == "admin":
        rows = await session.execute(select(Library).order_by(Library.created_at))
        libs = list(rows.scalars().all())
        for lib in libs:
            lib.my_access = "manage"
        return libs

    gids = await user_group_ids(session, user)
    conds = [Library.owner_id == user.id]
    if gids:
        conds.append(Library.owner_group_id.in_(gids))
        conds.append(
            Library.id.in_(
                select(LibraryShare.library_id).where(LibraryShare.group_id.in_(gids))
            )
        )
    rows = await session.execute(
        select(Library).where(or_(*conds)).order_by(Library.created_at)
    )
    libs = list(rows.scalars().unique().all())
    for lib in libs:
        lib.my_access = await library_access_level(session, user, lib)
    return libs


@router.post("", response_model=LibraryOut, status_code=201)
async def create_library(
    payload: LibraryCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if payload.owner_group_id is not None:
        gids = await user_group_ids(session, user)
        if payload.owner_group_id not in gids and user.org_role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a member of that group")
        lib = Library(
            name=payload.name,
            description=payload.description,
            owner_group_id=payload.owner_group_id,
        )
    else:
        lib = Library(name=payload.name, description=payload.description, owner_id=user.id)
    session.add(lib)
    await session.commit()
    await session.refresh(lib)
    lib.my_access = "manage"
    return lib


@router.get("/{library_id}", response_model=LibraryOut)
async def get_library(
    lib: Library = Depends(library_viewer),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    lib.my_access = await library_access_level(session, user, lib)
    return lib


@router.get("/{library_id}/activity", response_model=list[AuditEventOut])
async def library_activity(
    lib: Library = Depends(library_viewer),
    session: AsyncSession = Depends(get_session),
    limit: int = 50,
):
    rows = await session.execute(
        select(AuditEvent)
        .where(AuditEvent.library_id == lib.id)
        .order_by(AuditEvent.occurred_at.desc())
        .limit(min(limit, 200))
    )
    return list(rows.scalars().all())
