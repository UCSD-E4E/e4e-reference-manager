import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import embeddings
from ..audit import item_snapshot, record
from ..auth import get_current_user
from ..bibtex import year_from_csl
from ..db import get_session
from ..deps import item_editor, item_viewer, library_editor, library_viewer
from ..models import AuditEvent, Item, Library, User
from ..schemas import AuditEventOut, ItemCreate, ItemList, ItemOut, ItemUpdate

router = APIRouter(tags=["items"])


def _search_text(item: Item, csl: dict) -> str:
    """The searchable body (everything but the title, which is weighted separately)."""
    parts: list[str] = []
    if csl.get("abstract"):
        parts.append(str(csl["abstract"]))
    for author in csl.get("author") or []:
        name = " ".join(p for p in (author.get("given"), author.get("family")) if p)
        if name:
            parts.append(name)
    for key in ("container-title", "publisher"):
        if csl.get(key):
            parts.append(str(csl[key]))
    if item.citation_key:
        parts.append(item.citation_key)
    return "\n".join(parts)


def _denormalize(item: Item, csl: dict) -> None:
    item.csl_json = csl
    item.title = (csl.get("title") or "").strip()
    item.year = year_from_csl(csl)
    item.doi = csl.get("DOI")
    if csl.get("type"):
        item.type = csl["type"]  # csl is the source of truth for CSL type
    item.search_text = _search_text(item, csl)


def apply_changes(
    item: Item,
    *,
    csl: dict | None = None,
    citation_key: str | None = None,
    type: str | None = None,
) -> bool:
    """Apply edits to an existing item. Returns True if anything changed.

    An imported item keeps its original BibTeX so export can re-emit it verbatim — but
    only while that text still describes the item. Once its key, type or CSL changes,
    the original is dropped and export regenerates the entry from the current data."""
    changed = False
    if citation_key is not None and citation_key != item.citation_key:
        item.citation_key = citation_key
        changed = True
    if type is not None and type != item.type:
        item.type = type
        changed = True
    if csl is not None and csl != (item.csl_json or {}):
        _denormalize(item, csl)
        changed = True
    if changed:
        item.raw_bibtex = None
    return changed


@router.get("/libraries/{library_id}/items", response_model=ItemList)
async def list_items(
    lib: Library = Depends(library_viewer),
    session: AsyncSession = Depends(get_session),
    q: str | None = Query(None, description="Full-text search (title, abstract, authors)"),
    limit: int = Query(50, le=200),
    offset: int = 0,
):
    base = select(Item).where(Item.library_id == lib.id)
    order = Item.created_at.desc()
    if q and q.strip():
        # Postgres full-text search: stems + lowercases, ranks title (weight A) above body.
        tsquery = func.websearch_to_tsquery("english", q)
        base = base.where(Item.search_tsv.op("@@")(tsquery))
        order = func.ts_rank(Item.search_tsv, tsquery).desc()
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    rows = await session.execute(base.order_by(order).limit(limit).offset(offset))
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
    vec = await embeddings.embed_item_csl(payload.csl_json)  # best-effort; None if Ollama down
    if vec is not None:
        item.embedding = vec
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
    csl_changed = payload.csl_json is not None and payload.csl_json != (item.csl_json or {})
    apply_changes(
        item, csl=payload.csl_json, citation_key=payload.citation_key, type=payload.type
    )
    if csl_changed:
        vec = await embeddings.embed_item_csl(payload.csl_json)
        if vec is not None:
            item.embedding = vec
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
    apply_changes(
        item,
        csl=snapshot.get("csl_json"),
        citation_key=snapshot.get("citation_key"),
        type=snapshot.get("type"),
    )
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
