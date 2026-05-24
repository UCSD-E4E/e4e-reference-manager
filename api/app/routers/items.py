from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..bibtex import year_from_csl
from ..db import get_session
from ..deps import get_owned_item, get_owned_library
from ..models import Item, Library
from ..schemas import ItemCreate, ItemList, ItemOut, ItemUpdate

router = APIRouter(tags=["items"])


def _denormalize(item: Item, csl: dict) -> None:
    item.csl_json = csl
    item.title = (csl.get("title") or "").strip()
    item.year = year_from_csl(csl)
    item.doi = csl.get("DOI")


@router.get("/libraries/{library_id}/items", response_model=ItemList)
async def list_items(
    lib: Library = Depends(get_owned_library),
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
    lib: Library = Depends(get_owned_library),
    session: AsyncSession = Depends(get_session),
):
    item = Item(library_id=lib.id, citation_key=payload.citation_key, type=payload.type)
    _denormalize(item, payload.csl_json)
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


@router.get("/items/{item_id}", response_model=ItemOut)
async def get_item(item: Item = Depends(get_owned_item)):
    return item


@router.patch("/items/{item_id}", response_model=ItemOut)
async def update_item(
    payload: ItemUpdate,
    item: Item = Depends(get_owned_item),
    session: AsyncSession = Depends(get_session),
):
    if payload.version != item.version:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Version conflict: item is at version {item.version}, you sent {payload.version}",
        )
    if payload.citation_key is not None:
        item.citation_key = payload.citation_key
    if payload.type is not None:
        item.type = payload.type
    if payload.csl_json is not None:
        _denormalize(item, payload.csl_json)
    item.version += 1
    await session.commit()
    await session.refresh(item)
    return item


@router.delete("/items/{item_id}", status_code=204)
async def delete_item(
    item: Item = Depends(get_owned_item), session: AsyncSession = Depends(get_session)
):
    await session.delete(item)
    await session.commit()
