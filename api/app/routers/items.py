import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import item_snapshot, record
from ..auth import get_current_user
from ..bibtex import year_from_csl
from ..db import get_session
from ..deps import item_editor, item_viewer, library_editor, library_viewer
from ..models import AuditEvent, Item, Library, User
from ..schemas import AuditEventOut, ItemCreate, ItemList, ItemOut, ItemUpdate

router = APIRouter(tags=["items"])


def _denormalize(item: Item, csl: dict) -> None:
    item.csl_json = csl
    item.title = (csl.get("title") or "").strip()
    item.year = year_from_csl(csl)
    item.doi = csl.get("DOI")


@router.get("/libraries/{library_id}/items", response_model=ItemList)
async def list_items(
    lib: Library = Depends(library_viewer),
    session: AsyncSession = Depends(get_session),
    q: str | None = Query(None, description="Search in title"),
    limit: int = Query(50, le=200),
    offset: int = 0,
):
    base = select(Item).where(Item.library_id == lib.id)
    if q:
        base = base.where(Item.title.ilike(f"%{q}%"))
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    rows = await session.execute(
        base.order_by(Item.created_at.desc()).limit(limit).offset(offset)
    )
    return ItemList(total=total or 0, items=list(rows.scalars().all()))


@router.post("/libraries/{library_id}/items", response_model=ItemOut, status_code=201)
async def create_item(
    payload: ItemCreate,
    lib: Library = Depends(library_editor),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    item = Item(library_id=lib.id, citation_key=payload.citation_key, type=payload.type)
    _denormalize(item, payload.csl_json)
    session.add(item)
    await session.flush()
    record(
        session,
        actor=user,
        library_id=lib.id,
        entity_type="item",
        entity_id=item.id,
        operation="create",
        summary=f"Created “{item.title or item.citation_key}”",
        after=item_snapshot(item),
    )
    await session.commit()
    await session.refresh(item)
    return item


@router.get("/items/{item_id}", response_model=ItemOut)
async def get_item(item: Item = Depends(item_viewer)):
    return item


@router.patch("/items/{item_id}", response_model=ItemOut)
async def update_item(
    payload: ItemUpdate,
    item: Item = Depends(item_editor),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if payload.version != item.version:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Version conflict: item is at version {item.version}, you sent {payload.version}",
        )
    before = item_snapshot(item)
    if payload.citation_key is not None:
        item.citation_key = payload.citation_key
    if payload.type is not None:
        item.type = payload.type
    if payload.csl_json is not None:
        _denormalize(item, payload.csl_json)
    item.version += 1
    record(
        session,
        actor=user,
        library_id=item.library_id,
        entity_type="item",
        entity_id=item.id,
        operation="update",
        summary=f"Edited “{item.title or item.citation_key}”",
        before=before,
        after=item_snapshot(item),
    )
    await session.commit()
    await session.refresh(item)
    return item


@router.delete("/items/{item_id}", status_code=204)
async def delete_item(
    item: Item = Depends(item_editor),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    record(
        session,
        actor=user,
        library_id=item.library_id,
        entity_type="item",
        entity_id=item.id,
        operation="delete",
        summary=f"Deleted “{item.title or item.citation_key}”",
        before=item_snapshot(item),
    )
    await session.delete(item)
    await session.commit()


@router.get("/items/{item_id}/history", response_model=list[AuditEventOut])
async def item_history(
    item: Item = Depends(item_viewer), session: AsyncSession = Depends(get_session)
):
    rows = await session.execute(
        select(AuditEvent)
        .where(AuditEvent.entity_type == "item", AuditEvent.entity_id == item.id)
        .order_by(AuditEvent.occurred_at.desc())
    )
    return list(rows.scalars().all())


@router.post("/items/{item_id}/restore", response_model=ItemOut)
async def restore_item(
    event_id: uuid.UUID,
    item: Item = Depends(item_editor),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Restore the item to the state captured by a prior audit event (its `after`)."""
    event = await session.get(AuditEvent, event_id)
    if event is None or event.entity_id != item.id or event.entity_type != "item":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "History event not found for this item")
    snapshot = event.after or event.before
    if not snapshot:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That event has no restorable snapshot")

    before = item_snapshot(item)
    item.citation_key = snapshot.get("citation_key", item.citation_key)
    item.type = snapshot.get("type", item.type)
    _denormalize(item, snapshot.get("csl_json", item.csl_json))
    item.version += 1
    record(
        session,
        actor=user,
        library_id=item.library_id,
        entity_type="item",
        entity_id=item.id,
        operation="restore",
        summary=f"Restored to version {snapshot.get('version', '?')}",
        before=before,
        after=item_snapshot(item),
    )
    await session.commit()
    await session.refresh(item)
    return item
