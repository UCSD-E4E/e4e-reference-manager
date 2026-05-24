import hashlib
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..db import get_session
from ..deps import get_owned_item
from ..models import Attachment, Item, Library, User
from ..schemas import AttachmentOut
from ..storage import presigned_get_url, upload_bytes

router = APIRouter(tags=["attachments"])


@router.post("/items/{item_id}/attachments", response_model=AttachmentOut, status_code=201)
async def upload_attachment(
    file: UploadFile,
    item: Item = Depends(get_owned_item),
    session: AsyncSession = Depends(get_session),
):
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")
    sha256 = hashlib.sha256(data).hexdigest()
    content_type = file.content_type or "application/pdf"
    key = f"{item.library_id}/{item.id}/{sha256}"

    await run_in_threadpool(upload_bytes, key, data, content_type)

    att = Attachment(
        item_id=item.id,
        filename=file.filename or "document.pdf",
        content_type=content_type,
        size=len(data),
        sha256=sha256,
        storage_key=key,
    )
    session.add(att)
    await session.commit()
    await session.refresh(att)
    return att


async def _owned_attachment(
    attachment_id: uuid.UUID, user: User, session: AsyncSession
) -> Attachment:
    att = await session.get(Attachment, attachment_id)
    if att is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    item = await session.get(Item, att.item_id)
    lib = await session.get(Library, item.library_id) if item else None
    if lib is None or lib.owner_id != user.id:
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
