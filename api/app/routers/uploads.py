"""One-time PDF upload links.

Someone with edit access asks for a link for an item; whoever holds the link (typically
an agent running `curl -F file=@paper.pdf <url>`) can upload exactly one PDF to that
item within LINK_LIFETIME, with no session or API token. The file goes straight from
disk to the server, never through a model's context.

The link's secret is the credential, so only its hash is stored, and at upload time
the creator's edit access is checked again (a revoked share kills outstanding links).
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..config import get_settings
from ..db import get_session
from ..deps import _rank, item_editor, library_access_level
from ..models import Item, Library, PdfUploadLink, User
from ..schemas import AttachmentOut, PdfUploadLinkOut
from ..upload_limits import read_pdf_upload
from .attachments import _store_attachment

router = APIRouter(tags=["attachments"])

LINK_LIFETIME = timedelta(minutes=15)


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


@router.post(
    "/items/{item_id}/upload-links",
    response_model=PdfUploadLinkOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_upload_link(
    item: Item = Depends(item_editor),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    now = datetime.now(timezone.utc)
    # Housekeeping: links are useless a day after they expire.
    await session.execute(
        delete(PdfUploadLink).where(PdfUploadLink.expires_at < now - timedelta(days=1))
    )
    secret = secrets.token_urlsafe(32)
    link = PdfUploadLink(
        item_id=item.id,
        created_by_id=user.id,
        token_hash=_hash(secret),
        expires_at=now + LINK_LIFETIME,
    )
    session.add(link)
    await session.commit()
    url = f"{get_settings().app_base_url.rstrip('/')}/uploads/{secret}"
    return PdfUploadLinkOut(
        url=url, expires_at=link.expires_at, curl=f"curl -fsS -F file=@paper.pdf '{url}'"
    )


@router.post(
    "/uploads/{secret}", response_model=AttachmentOut, status_code=status.HTTP_201_CREATED
)
async def upload_with_link(
    secret: str, file: UploadFile, session: AsyncSession = Depends(get_session)
):
    """Attach one PDF using a one-time link (no other authentication)."""
    now = datetime.now(timezone.utc)
    link = await session.scalar(
        select(PdfUploadLink).where(PdfUploadLink.token_hash == _hash(secret))
    )
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown upload link")
    if link.used_at is not None or link.expires_at <= now:
        raise HTTPException(status.HTTP_410_GONE, "This upload link was used or has expired")

    item = await session.get(Item, link.item_id)  # None if it's in the trash
    if item is None:
        raise HTTPException(status.HTTP_410_GONE, "The reference no longer exists")
    creator = await session.get(User, link.created_by_id)
    lib = await session.get(Library, item.library_id)
    level = await library_access_level(session, creator, lib) if creator and lib else None
    if _rank(level) < _rank("edit"):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "The link's creator no longer has edit access"
        )

    data = await read_pdf_upload(file)
    if not data.startswith(b"%PDF"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only PDF files can be uploaded")

    # Use the link up atomically: of two concurrent uploads only one gets the row.
    claimed = await session.execute(
        update(PdfUploadLink)
        .where(
            PdfUploadLink.id == link.id,
            PdfUploadLink.used_at.is_(None),
            PdfUploadLink.expires_at > now,
        )
        .values(used_at=now)
    )
    if claimed.rowcount != 1:
        raise HTTPException(status.HTTP_410_GONE, "This upload link was used or has expired")
    # Commits the claim together with the attachment; a failed store rolls both back.
    return await _store_attachment(
        session, item, data, file.filename or "paper.pdf", "application/pdf"
    )
