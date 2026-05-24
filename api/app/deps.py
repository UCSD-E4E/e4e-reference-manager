"""Access control: resolve a user's access level on a Library and gate routes by it.

Access levels (ascending): view < edit < manage.
A user's level on a library is the highest of:
  - org admin                      -> manage (everything)
  - user owns the library          -> manage
  - member of the owning group     -> manage
  - library shared to one of the   -> the share's access_level
    user's groups
"""
from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_user
from .db import get_session
from .models import _ACCESS_RANK, Item, Library, LibraryShare, User, user_group


def _rank(level: str | None) -> int:
    return _ACCESS_RANK.get(level or "", 0)


async def user_group_ids(session: AsyncSession, user: User) -> set[uuid.UUID]:
    rows = await session.execute(
        select(user_group.c.group_id).where(user_group.c.user_id == user.id)
    )
    return set(rows.scalars())


async def library_access_level(
    session: AsyncSession, user: User, library: Library
) -> str | None:
    if user.org_role == "admin":
        return "manage"
    if library.owner_id is not None and library.owner_id == user.id:
        return "manage"
    gids = await user_group_ids(session, user)
    if library.owner_group_id is not None and library.owner_group_id in gids:
        return "manage"
    if gids:
        rows = await session.execute(
            select(LibraryShare.access_level).where(
                LibraryShare.library_id == library.id,
                LibraryShare.group_id.in_(gids),
            )
        )
        levels = list(rows.scalars())
        if levels:
            return max(levels, key=_rank)
    return None


def require_library_access(min_level: str = "view"):
    """Dependency factory: resolves `library_id` path param and enforces `min_level`."""

    async def dep(
        library_id: uuid.UUID,
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_session),
    ) -> Library:
        lib = await session.get(Library, library_id)
        if lib is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Library not found")
        level = await library_access_level(session, user, lib)
        if level is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Library not found")
        if _rank(level) < _rank(min_level):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {min_level} access")
        return lib

    return dep


def require_item_access(min_level: str = "view"):
    """Dependency factory: resolves `item_id` path param via its library and enforces level."""

    async def dep(
        item_id: uuid.UUID,
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_session),
    ) -> Item:
        item = await session.get(Item, item_id)
        if item is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
        lib = await session.get(Library, item.library_id)
        if lib is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
        level = await library_access_level(session, user, lib)
        if level is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
        if _rank(level) < _rank(min_level):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {min_level} access")
        return item

    return dep


# Convenience singletons
library_viewer = require_library_access("view")
library_editor = require_library_access("edit")
library_manager = require_library_access("manage")
item_viewer = require_item_access("view")
item_editor = require_item_access("edit")
