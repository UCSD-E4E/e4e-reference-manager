"""A library's trash: references deleted in the last 30 days, restorable until purged."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..db import get_session
from ..deps import library_editor, library_viewer
from ..models import Item, Library, User
from ..schemas import ItemOut, TrashedItemOut, UserOut
from ..trash import purge, purge_at, restore_from_trash

router = APIRouter(prefix="/libraries/{library_id}/trash", tags=["trash"])


def _trashed(lib: Library):
    return (
        select(Item)
        .where(Item.library_id == lib.id, Item.deleted_at.is_not(None))
        .execution_options(include_trashed=True)
    )


async def _trashed_item(session: AsyncSession, lib: Library, item_id: uuid.UUID) -> Item:
    item = await session.scalar(_trashed(lib).where(Item.id == item_id))
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not in this library's trash")
    return item


@router.get("", response_model=list[TrashedItemOut])
async def list_trash(
    lib: Library = Depends(library_viewer), session: AsyncSession = Depends(get_session)
):
    items = list(
        (await session.execute(_trashed(lib).order_by(Item.deleted_at.desc()))).scalars()
    )
    user_ids = {i.deleted_by_id for i in items if i.deleted_by_id}
    users = (
        {u.id: u for u in (await session.execute(select(User).where(User.id.in_(user_ids))))
         .scalars()}
        if user_ids
        else {}
    )
    return [
        TrashedItemOut(
            item=ItemOut.model_validate(i),
            deleted_at=i.deleted_at,
            deleted_by=UserOut.model_validate(users[i.deleted_by_id])
            if i.deleted_by_id in users
            else None,
            purge_at=purge_at(i),
        )
        for i in items
    ]


@router.post("/{item_id}/restore", response_model=ItemOut)
async def restore(
    item_id: uuid.UUID,
    lib: Library = Depends(library_editor),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    item = await _trashed_item(session, lib, item_id)
    restore_from_trash(session, item, user)
    await session.commit()
    await session.refresh(item)
    return item


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_forever(
    item_id: uuid.UUID,
    lib: Library = Depends(library_editor),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await purge(session, await _trashed_item(session, lib, item_id), user)
    await session.commit()


@router.delete("")
async def empty_trash(
    lib: Library = Depends(library_editor),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    items = list((await session.execute(_trashed(lib))).scalars())
    for item in items:
        await purge(session, item, user)
    await session.commit()
    return {"purged": len(items)}
