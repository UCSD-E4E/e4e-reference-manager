"""Phase 4: Collections (nestable folders within a Library) + per-collection .bib export.

A collection groups items that already belong to its library. RBAC follows the library:
`view` to read/export, `edit` to create/modify."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..db import get_session
from ..deps import _rank, library_access_level, library_editor, library_viewer
from ..models import Collection, Item, Library, User, item_collection
from ..schemas import CollectionCreate, CollectionOut, ItemOut
from .bib import _bib_response

router = APIRouter(tags=["collections"])


async def _collection_for(
    collection_id: uuid.UUID, user: User, session: AsyncSession, min_level: str
) -> Collection:
    coll = await session.get(Collection, collection_id)
    lib = await session.get(Library, coll.library_id) if coll else None
    level = await library_access_level(session, user, lib) if lib else None
    if coll is None or level is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collection not found")
    if _rank(level) < _rank(min_level):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {min_level} access")
    return coll


@router.get("/libraries/{library_id}/collections", response_model=list[CollectionOut])
async def list_collections(
    lib: Library = Depends(library_viewer), session: AsyncSession = Depends(get_session)
):
    rows = await session.execute(
        select(Collection).where(Collection.library_id == lib.id).order_by(Collection.name)
    )
    return list(rows.scalars().all())


@router.post("/libraries/{library_id}/collections", response_model=CollectionOut, status_code=201)
async def create_collection(
    payload: CollectionCreate,
    lib: Library = Depends(library_editor),
    session: AsyncSession = Depends(get_session),
):
    if payload.parent_id is not None:
        parent = await session.get(Collection, payload.parent_id)
        if parent is None or parent.library_id != lib.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "parent_id is not in this library")
    coll = Collection(library_id=lib.id, name=payload.name, parent_id=payload.parent_id)
    session.add(coll)
    await session.commit()
    await session.refresh(coll)
    return coll


@router.delete("/collections/{collection_id}", status_code=204)
async def delete_collection(
    collection_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    coll = await _collection_for(collection_id, user, session, "edit")
    await session.delete(coll)
    await session.commit()


@router.get("/collections/{collection_id}/items", response_model=list[ItemOut])
async def list_collection_items(
    collection_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    coll = await _collection_for(collection_id, user, session, "view")
    rows = await session.execute(
        select(Item)
        .join(item_collection, item_collection.c.item_id == Item.id)
        .where(item_collection.c.collection_id == coll.id)
        .order_by(Item.citation_key)
    )
    return list(rows.scalars().all())


@router.post("/collections/{collection_id}/items/{item_id}", status_code=204)
async def add_item_to_collection(
    collection_id: uuid.UUID,
    item_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    coll = await _collection_for(collection_id, user, session, "edit")
    item = await session.get(Item, item_id)
    if item is None or item.library_id != coll.library_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Item is not in this collection's library")
    already = await session.execute(
        select(item_collection).where(
            item_collection.c.collection_id == coll.id, item_collection.c.item_id == item.id
        )
    )
    if already.first() is None:  # idempotent add
        await session.execute(
            insert(item_collection).values(collection_id=coll.id, item_id=item.id)
        )
        await session.commit()


@router.delete("/collections/{collection_id}/items/{item_id}", status_code=204)
async def remove_item_from_collection(
    collection_id: uuid.UUID,
    item_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    coll = await _collection_for(collection_id, user, session, "edit")
    await session.execute(
        delete(item_collection).where(
            item_collection.c.collection_id == coll.id, item_collection.c.item_id == item_id
        )
    )
    await session.commit()


@router.get("/collections/{collection_id}/export.bib")
async def export_collection(
    collection_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    coll = await _collection_for(collection_id, user, session, "view")
    rows = await session.execute(
        select(Item)
        .join(item_collection, item_collection.c.item_id == Item.id)
        .where(item_collection.c.collection_id == coll.id)
        .order_by(Item.citation_key)
    )
    return _bib_response(list(rows.scalars().all()), f"{coll.name or 'collection'}.bib")
