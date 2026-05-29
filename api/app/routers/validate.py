"""Phase 5: anti-hallucination validation endpoints.

Per-item POST runs and stores a single verdict; the library-level POST batches over every
item and returns a summary. RBAC follows the library: edit access is required because
validation writes the verdict onto the item."""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from .. import grobid, validation
from ..auth import get_current_user
from ..db import get_session
from ..deps import item_editor, library_editor
from ..models import Attachment, Item, Library, User
from ..storage import download_bytes

router = APIRouter(tags=["validation"])


async def _validate_and_store(item: Item, session: AsyncSession) -> dict:
    verdict = await validation.validate_item_csl(item.csl_json or {})
    item.validation = verdict
    # JSONB mutation on an existing row isn't always autodetected by SQLAlchemy.
    flag_modified(item, "validation")
    return verdict


@router.post("/items/{item_id}/validate")
async def validate_item(
    item: Item = Depends(item_editor),
    session: AsyncSession = Depends(get_session),
):
    verdict = await _validate_and_store(item, session)
    await session.commit()
    return verdict


@router.post("/libraries/{library_id}/validate")
async def validate_library(
    lib: Library = Depends(library_editor),
    session: AsyncSession = Depends(get_session),
):
    rows = await session.execute(select(Item).where(Item.library_id == lib.id))
    items = list(rows.scalars().all())
    counts = {
        "verified": 0,
        "metadata_mismatch": 0,
        "not_found": 0,
        "unverifiable": 0,
    }
    for item in items:
        verdict = await _validate_and_store(item, session)
        counts[verdict["status"]] = counts.get(verdict["status"], 0) + 1
    await session.commit()
    return {"checked": len(items), **counts}


# ---------- Phase 6: validate the bibliography *inside* a PDF ----------

_EMPTY_COUNTS = {"verified": 0, "metadata_mismatch": 0, "not_found": 0, "unverifiable": 0}


async def _validate_pdf_bytes(pdf_bytes: bytes) -> dict:
    """GROBID extracts the citations; each one is validated via Crossref/arXiv. Returns
    {summary, references[]} with per-citation cited_text + csl + verdict."""
    refs = await grobid.extract_references(pdf_bytes)
    counts = dict(_EMPTY_COUNTS)
    results: list[dict] = []
    for ref in refs:
        verdict = await validation.validate_item_csl(ref)
        counts[verdict["status"]] = counts.get(verdict["status"], 0) + 1
        cited_text = ref.pop("_raw", "") or ref.get("title", "")
        results.append({"cited_text": cited_text, "csl": ref, "verdict": verdict})
    return {"summary": {"total": len(refs), **counts}, "references": results}


@router.post("/items/{item_id}/validate-references")
async def validate_item_references(
    item: Item = Depends(item_editor),
    session: AsyncSession = Depends(get_session),
):
    """Pull the first PDF attached to the item, extract its bibliography with GROBID,
    and verify each citation against Crossref/arXiv."""
    rows = await session.execute(
        select(Attachment)
        .where(Attachment.item_id == item.id, Attachment.content_type == "application/pdf")
        .order_by(Attachment.created_at)
    )
    pdf = rows.scalars().first()
    if pdf is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This item has no PDF attachment to scan for citations",
        )
    data = await run_in_threadpool(download_bytes, pdf.storage_key)
    return await _validate_pdf_bytes(data)


@router.post("/pdf-validate")
async def validate_pdf_upload(
    file: UploadFile,
    _user: User = Depends(get_current_user),
):
    """Upload an arbitrary PDF (no Item required) and get a verdict for every citation
    in its bibliography. Useful for vetting an AI-drafted paper before importing."""
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")
    return await _validate_pdf_bytes(data)
