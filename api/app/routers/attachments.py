import asyncio
import hashlib
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import oa_pdf
from ..auth import get_current_user
from ..db import get_session
from ..deps import item_editor, library_access_level, library_editor
from ..models import _ACCESS_RANK, Attachment, Item, Library, User
from ..pdf import extract_pdf_text
from ..schemas import AttachmentOut, FetchedPdf, LibraryPdfFetch
from ..storage import download_bytes, presigned_get_url, upload_bytes

logger = logging.getLogger("refman")

router = APIRouter(tags=["attachments"])


async def _refresh_item_pdf_text(session: AsyncSession, item: Item) -> None:
    """Recompute the owning item's concatenated PDF text (drives the FTS tsvector)."""
    rows = await session.execute(
        select(Attachment.text).where(Attachment.item_id == item.id)
    )
    item.pdf_text = "\n\n".join(t for (t,) in rows.all() if t)


async def _store_attachment(
    session: AsyncSession, item: Item, data: bytes, filename: str, content_type: str
) -> Attachment:
    """Upload bytes to object storage, extract PDF text, and attach them to the item.
    Commits. Content-addressed key, so identical bytes share one stored object."""
    sha256 = hashlib.sha256(data).hexdigest()
    key = f"{item.library_id}/{item.id}/{sha256}"

    await run_in_threadpool(upload_bytes, key, data, content_type)

    text = (
        await run_in_threadpool(extract_pdf_text, data)
        if content_type == "application/pdf"
        else None
    )
    att = Attachment(
        item_id=item.id,
        filename=filename,
        content_type=content_type,
        size=len(data),
        sha256=sha256,
        storage_key=key,
        text=text,
    )
    session.add(att)
    await session.flush()
    await _refresh_item_pdf_text(session, item)
    await session.commit()
    await session.refresh(att)
    return att


@router.post("/items/{item_id}/attachments", response_model=AttachmentOut, status_code=201)
async def upload_attachment(
    file: UploadFile,
    item: Item = Depends(item_editor),
    session: AsyncSession = Depends(get_session),
):
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")
    return await _store_attachment(
        session,
        item,
        data,
        file.filename or "document.pdf",
        file.content_type or "application/pdf",
    )


@router.post("/items/{item_id}/fetch-pdf", response_model=FetchedPdf, status_code=201)
async def fetch_pdf(
    response: Response,
    item: Item = Depends(item_editor),
    session: AsyncSession = Depends(get_session),
):
    """Find a legal open-access copy online (Unpaywall by DOI, then arXiv) and attach it."""
    found = await oa_pdf.fetch_open_access_pdf(item.csl_json or {})
    if found is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No open-access PDF found (Unpaywall, arXiv). Paywalled papers can't be "
            "fetched; upload the PDF instead.",
        )
    data, source, url = found
    att, created = await _attach_fetched_pdf(session, item, data)
    if not created:
        response.status_code = status.HTTP_200_OK
    return FetchedPdf(attachment=att, source=source, url=url)


async def _attach_fetched_pdf(
    session: AsyncSession, item: Item, data: bytes
) -> tuple[Attachment, bool]:
    """Attach downloaded PDF bytes unless the identical file is already on the item.
    Returns (attachment, created)."""
    sha256 = hashlib.sha256(data).hexdigest()
    existing = next((a for a in item.attachments if a.sha256 == sha256), None)
    if existing is not None:
        return existing, False
    att = await _store_attachment(
        session, item, data, f"{item.citation_key or 'paper'}.pdf", "application/pdf"
    )
    return att, True


def _is_pdf(att: Attachment) -> bool:
    """Browsers don't always send application/pdf (octet-stream, parameters, no type)."""
    base = (att.content_type or "").split(";")[0].strip().lower()
    return base == "application/pdf" or att.filename.lower().endswith(".pdf")


FETCH_CONCURRENCY = 4


@router.post("/libraries/{library_id}/fetch-pdfs", response_model=LibraryPdfFetch)
async def fetch_pdfs(
    after: uuid.UUID | None = None,
    limit: int = Query(10, ge=1, le=50),
    lib: Library = Depends(library_editor),
    session: AsyncSession = Depends(get_session),
):
    """Find and attach an open-access PDF for one batch of the library's items (ordered
    by id, after the `after` cursor). Items that already have a PDF are skipped, so it's
    safe to re-run. Batches keep each request short; the client loops on `next_after`.

    Lookups/downloads run a few at a time; DB writes go one at a time (one session), and
    each item commits on its own so one failure doesn't lose the others."""
    query = select(Item).where(Item.library_id == lib.id).order_by(Item.id).limit(limit)
    if after is not None:
        query = query.where(Item.id > after)
    items = list((await session.execute(query)).scalars().all())

    counts = dict.fromkeys(
        ("attached", "already_had_pdf", "not_found", "no_identifier", "failed"), 0
    )
    # Snapshot what the network step and the response need: a failed item's rollback
    # expires every loaded object, and async sessions can't lazily refresh them.
    next_after = items[-1].id if len(items) == limit else None
    todo: list[tuple[uuid.UUID, dict]] = []
    for item in items:
        csl = item.csl_json or {}
        if any(_is_pdf(a) for a in item.attachments):
            counts["already_had_pdf"] += 1
        elif not oa_pdf.has_identifier(csl):
            counts["no_identifier"] += 1
        else:
            todo.append((item.id, csl))

    fetch_slots = asyncio.Semaphore(FETCH_CONCURRENCY)
    db_lock = asyncio.Lock()

    async def process(item_id: uuid.UUID, csl: dict) -> None:
        async with fetch_slots:
            try:
                found = await oa_pdf.fetch_open_access_pdf(csl)
            except Exception:
                logger.exception("Open-access lookup failed for item %s", item_id)
                counts["failed"] += 1
                return
            if found is None:
                counts["not_found"] += 1
                return
            async with db_lock:
                try:
                    # Fresh copy: picks up attachments added since the batch started
                    # (e.g. a concurrent run) so the sha256 dedupe sees them.
                    item = await session.get(Item, item_id, populate_existing=True)
                    if item is None:  # deleted meanwhile
                        return
                    _att, created = await _attach_fetched_pdf(session, item, found[0])
                    counts["attached" if created else "already_had_pdf"] += 1
                except Exception:
                    logger.exception("Storing fetched PDF failed for item %s", item_id)
                    await session.rollback()
                    counts["failed"] += 1

    await asyncio.gather(*(process(item_id, csl) for item_id, csl in todo))
    return LibraryPdfFetch(
        checked=len(items),
        **counts,
        next_after=next_after,
    )


async def _owned_attachment(
    attachment_id: uuid.UUID, user: User, session: AsyncSession
) -> Attachment:
    att = await session.get(Attachment, attachment_id)
    if att is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    item = await session.get(Item, att.item_id)
    lib = await session.get(Library, item.library_id) if item else None
    if lib is None or await library_access_level(session, user, lib) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    return att


@router.get("/attachments/{attachment_id}/url")
async def attachment_url(
    attachment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Signed URL for the in-app PDF.js viewer (inline)."""
    att = await _owned_attachment(attachment_id, user, session)
    url = await run_in_threadpool(presigned_get_url, att.storage_key, None)
    return {"url": url, "content_type": att.content_type}


@router.get("/attachments/{attachment_id}/download")
async def download_attachment(
    attachment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Redirect to a signed URL that forces a download with the original filename."""
    att = await _owned_attachment(attachment_id, user, session)
    url = await run_in_threadpool(presigned_get_url, att.storage_key, att.filename)
    return RedirectResponse(url)


@router.post("/attachments/{attachment_id}/extract-text", response_model=AttachmentOut)
async def reextract_text(
    attachment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Re-download the attachment and refresh its cached text + the item's FTS index.
    Requires edit access on the library (it mutates the search index)."""
    att = await _owned_attachment(attachment_id, user, session)
    item = await session.get(Item, att.item_id)
    lib = await session.get(Library, item.library_id)
    level = await library_access_level(session, user, lib)
    if _ACCESS_RANK.get(level or "", 0) < _ACCESS_RANK["edit"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Edit access required")

    data = await run_in_threadpool(download_bytes, att.storage_key)
    att.text = (
        await run_in_threadpool(extract_pdf_text, data)
        if att.content_type == "application/pdf"
        else None
    )
    await session.flush()
    await _refresh_item_pdf_text(session, item)
    await session.commit()
    await session.refresh(att)
    return att
