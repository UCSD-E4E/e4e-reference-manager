"""Shared route dependencies: ownership-scoped lookups."""
from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_user
from .db import get_session
from .models import Item, Library, User


async def get_owned_library(
    library_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Library:
    lib = await session.get(Library, library_id)
    if lib is None or lib.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Library not found")
    return lib


async def get_owned_item(
    item_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Item:
    item = await session.get(Item, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    lib = await session.get(Library, item.library_id)
    if lib is None or lib.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    return item
