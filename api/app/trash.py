"""Library trash for references.

Deleting a reference sets `Item.deleted_at` instead of removing the row. A session-wide
ORM rule hides trashed items from *every* SELECT (lists, search, collections, exports,
dedupe, item endpoints...), so no query can forget the filter. Code that must see the
trash opts in with `.execution_options(include_trashed=True)`.

Trashed items are purged (really deleted, with their notes and attachments) once
TRASH_RETENTION has passed; the API runs `purge_expired_trash` periodically.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from .audit import item_snapshot, record
from .models import Item, User

logger = logging.getLogger("refman")

TRASH_RETENTION = timedelta(days=30)
PURGE_INTERVAL_SECONDS = 6 * 3600


@event.listens_for(Session, "do_orm_execute")
def _hide_trashed_items(state: ORMExecuteState) -> None:
    if (
        state.is_select
        # Refreshing attributes of an item already in hand must still work.
        and not state.is_column_load
        and not state.execution_options.get("include_trashed", False)
    ):
        state.statement = state.statement.options(
            with_loader_criteria(Item, lambda cls: cls.deleted_at.is_(None), include_aliases=True)
        )


def purge_at(item: Item) -> datetime | None:
    return item.deleted_at + TRASH_RETENTION if item.deleted_at else None


def move_to_trash(session: AsyncSession, item: Item, user: User) -> None:
    item.deleted_at = datetime.now(timezone.utc)
    item.deleted_by_id = user.id
    record(
        session,
        actor=user,
        library_id=item.library_id,
        entity_type="item",
        entity_id=item.id,
        operation="delete",
        summary=f"Moved “{item.title or item.citation_key}” to the trash",
        before=item_snapshot(item),
    )


def restore_from_trash(session: AsyncSession, item: Item, user: User) -> None:
    item.deleted_at = None
    item.deleted_by_id = None
    record(
        session,
        actor=user,
        library_id=item.library_id,
        entity_type="item",
        entity_id=item.id,
        operation="restore",
        summary=f"Restored “{item.title or item.citation_key}” from the trash",
        after=item_snapshot(item),
    )


async def purge(session: AsyncSession, item: Item, user: User | None) -> None:
    """Delete a trashed item for good (notes, attachments and links go with it)."""
    record(
        session,
        actor=user,
        library_id=item.library_id,
        entity_type="item",
        entity_id=item.id,
        operation="purge",
        summary=f"Permanently deleted “{item.title or item.citation_key}”",
        before=item_snapshot(item),
    )
    await session.delete(item)


async def purge_expired_trash(session: AsyncSession, now: datetime | None = None) -> int:
    """Permanently delete items that have been in the trash longer than the retention
    period. Returns how many were purged."""
    cutoff = (now or datetime.now(timezone.utc)) - TRASH_RETENTION
    rows = await session.execute(
        select(Item)
        .where(Item.deleted_at.is_not(None), Item.deleted_at < cutoff)
        .execution_options(include_trashed=True)
    )
    items = list(rows.scalars().all())
    for item in items:
        await purge(session, item, None)
    await session.commit()
    return len(items)


async def purge_trash_forever(session_factory) -> None:
    """Background loop: purge expired trash now, then every PURGE_INTERVAL_SECONDS."""
    while True:
        try:
            async with session_factory() as session:
                if n := await purge_expired_trash(session):
                    logger.info("Purged %d item(s) from the trash", n)
        except Exception:
            logger.exception("Trash purge failed; will retry")
        await asyncio.sleep(PURGE_INTERVAL_SECONDS)
