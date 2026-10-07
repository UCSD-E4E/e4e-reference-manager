"""Phase 2 ingestion endpoints: identifier/URL import, PDF metadata extraction, merge."""
import hashlib
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import item_snapshot, record
from ..auth import get_current_user
from ..db import get_session
from ..dedupe import find_duplicate_groups, merge_into
from ..deps import item_editor, library_editor
from ..grobid import extract_header_csl
from ..ingest import IngestError, fetch_csl, gen_citation_key, normalize_doi
from ..models import Attachment, Item, Library, User
from ..schemas import (
    DedupeResult,
    IngestRequest,
    IngestResult,
    IngestResultItem,
    ItemOut,
    MetadataProposal,
)
from ..storage import download_bytes, upload_bytes
from .items import _denormalize

router = APIRouter(tags=["ingest"])


@router.post("/libraries/{library_id}/ingest", response_model=IngestResult)
async def ingest_identifier(
    payload: IngestRequest,
    lib: Library = Depends(library_editor),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Import references by DOI / arXiv / PMID / ISBN / URL via translation-server."""
    try:
        csl_list = await fetch_csl(payload.query)
    except IngestError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))

    # existing DOIs in this library for dedup
    rows = await session.execute(
        select(Item.id, Item.doi).where(Item.library_id == lib.id, Item.doi.isnot(None))
    )
    existing = {normalize_doi(doi): iid for iid, doi in rows if normalize_doi(doi)}

    results: list[IngestResultItem] = []
    for csl in csl_list:
        nd = normalize_doi(csl.get("DOI"))
        if nd and nd in existing:
            results.append(
                IngestResultItem(
                    status="duplicate",
                    item_id=existing[nd],
                    citation_key="",
                    title=csl.get("title", ""),
                    doi=nd,
                )
            )
            continue
        key = (csl.get("id") or "").strip() or gen_citation_key(csl)
        item = Item(library_id=lib.id, citation_key=key, type=csl.get("type", "document"))
        _denormalize(item, csl)
        session.add(item)
        await session.flush()
        record(
            session,
            actor=user,
            library_id=lib.id,
            entity_type="item",
            entity_id=item.id,
            operation="create",
            summary=f"Imported “{item.title or key}”",
            after=item_snapshot(item),
        )
        if nd:
            existing[nd] = item.id
        results.append(
            IngestResultItem(
                status="created", item_id=item.id, citation_key=key, title=item.title, doi=nd
            )
        )
    await session.commit()
    return IngestResult(results=results)


@router.post("/items/{item_id}/extract-metadata", response_model=MetadataProposal)
async def extract_metadata(
    item: Item = Depends(item_editor),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    apply: bool = Query(False, description="Merge extracted fields into the item"),
):
    """Run GROBID on the item's first PDF; optionally merge missing fields into the item."""
    if not item.attachments:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Item has no PDF attachment")
    pdf = await run_in_threadpool(download_bytes, item.attachments[0].storage_key)
    try:
        proposed = await extract_header_csl(pdf)
    except IngestError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))

    if not apply:
        return MetadataProposal(csl_json=proposed, applied=False, item=None)

    before = item_snapshot(item)
    merged = dict(item.csl_json or {})
    for k, v in proposed.items():
        if k == "type":
            continue  # don't override the curated type
        if not merged.get(k):
            merged[k] = v
    _denormalize(item, merged)
    item.version += 1
    record(
        session,
        actor=user,
        library_id=item.library_id,
        entity_type="item",
        entity_id=item.id,
        operation="update",
        summary="Filled metadata from PDF (GROBID)",
        before=before,
        after=item_snapshot(item),
    )
    await session.commit()
    await session.refresh(item)
    return MetadataProposal(csl_json=proposed, applied=True, item=ItemOut.model_validate(item))


@router.post("/libraries/{library_id}/items/from-pdf", response_model=ItemOut, status_code=201)
async def create_item_from_pdf(
    file: UploadFile,
    lib: Library = Depends(library_editor),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Create a reference from a PDF: store it and best-effort populate metadata via GROBID."""
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")

    csl: dict = {"type": "article-journal"}
    try:
        csl = await extract_header_csl(data) or csl
    except IngestError:
        pass  # best-effort; fall back to an empty record
    if not csl.get("title"):
        csl["title"] = (file.filename or "Untitled").rsplit(".", 1)[0]

    item = Item(library_id=lib.id, citation_key=gen_citation_key(csl), type=csl.get("type", "document"))
    _denormalize(item, csl)
    session.add(item)
    await session.flush()

    sha256 = hashlib.sha256(data).hexdigest()
    key = f"{lib.id}/{item.id}/{sha256}"
    await run_in_threadpool(upload_bytes, key, data, "application/pdf")
    session.add(
        Attachment(
            item_id=item.id,
            filename=file.filename or "document.pdf",
            content_type="application/pdf",
            size=len(data),
            sha256=sha256,
            storage_key=key,
        )
    )
    record(
        session,
        actor=user,
        library_id=lib.id,
        entity_type="item",
        entity_id=item.id,
        operation="create",
        summary=f"Created from PDF “{item.title}”",
        after=item_snapshot(item),
    )
    await session.commit()
    await session.refresh(item)
    return item


@router.post("/items/{item_id}/merge/{other_id}", response_model=ItemOut)
async def merge_items(
    other_id: uuid.UUID,
    item: Item = Depends(item_editor),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Merge `other` into `item`: move its PDFs and notes, then delete it."""
    if other_id == item.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot merge an item into itself")
    other = await session.get(Item, other_id)
    if other is None or other.library_id != item.library_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Other item not found in this library")

    record(
        session,
        actor=user,
        library_id=item.library_id,
        entity_type="item",
        entity_id=item.id,
        operation="update",
        summary=f"Merged “{other.title or other.citation_key}” into this item",
        before=item_snapshot(other),
        after=item_snapshot(item),
    )
    await merge_into(session, item, other)
    await session.commit()
    await session.refresh(item)
    return item


@router.post("/libraries/{library_id}/dedupe", response_model=DedupeResult)
async def dedupe_library(
    lib: Library = Depends(library_editor),
    dry_run: bool = Query(False, description="Report the duplicate groups without merging"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Merge duplicates already in the project (same rule as .bib import: DOI, else
    normalized title + year). The oldest copy of each paper is kept."""
    items = (
        await session.execute(
            select(Item).where(Item.library_id == lib.id).order_by(Item.created_at, Item.id)
        )
    ).scalars().all()
    groups = find_duplicate_groups(items)
    if not dry_run:
        by_id = {i.id: i for i in items}
        for g in groups:
            keep = by_id[g["kept"]]
            for other_id in g["merged"]:
                other = by_id[other_id]
                record(
                    session,
                    actor=user,
                    library_id=lib.id,
                    entity_type="item",
                    entity_id=keep.id,
                    operation="update",
                    summary=f"Merged duplicate “{other.title or other.citation_key}” "
                    f"(matched on {g['matched_on']})",
                    before=item_snapshot(other),
                    after=item_snapshot(keep),
                )
                await merge_into(session, keep, other)
        await session.commit()
    return DedupeResult(merged=sum(len(g["merged"]) for g in groups), groups=groups)
