from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..auth import get_current_user
from ..db import get_session
from ..deps import library_access_level, library_manager, library_viewer, user_group_ids
from ..models import AuditEvent, Item, Library, LibraryShare, User
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


@router.delete("/{library_id}", status_code=204)
async def delete_library(
    lib: Library = Depends(library_manager),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Delete a project and everything in it (items, collections, notes, annotations,
    shares, its own activity log) via the schema's ON DELETE CASCADE. PDFs stay in
    object storage, as for item deletes, so a database restore brings a project back
    whole."""
    item_count = await session.scalar(
        select(func.count()).select_from(Item).where(Item.library_id == lib.id)
    )
    # library_id=None: the library's own events cascade away with it; this one stays.
    record(
        session,
        actor=user,
        library_id=None,
        entity_type="library",
        entity_id=lib.id,
        operation="delete",
        summary=f"Deleted project “{lib.name}” ({item_count} items)",
        before={"name": lib.name, "description": lib.description, "items": item_count},
    )
    await session.execute(delete(Library).where(Library.id == lib.id))
    await session.commit()


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
