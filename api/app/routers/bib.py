import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..bibtex import build_bibtex, parse_bibtex
from ..db import get_session
from ..deps import get_owned_library
from ..models import BibFile, Item, Library, User
from ..routers.items import _denormalize
from ..schemas import ImportResult

router = APIRouter(tags=["bibtex"])


@router.post("/libraries/{library_id}/import", response_model=ImportResult)
async def import_bib(
    file: UploadFile,
    lib: Library = Depends(get_owned_library),
    session: AsyncSession = Depends(get_session),
):
    raw = (await file.read()).decode("utf-8", errors="replace")
    entries = parse_bibtex(raw)
    if not entries:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No BibTeX entries found")

    bib_file = BibFile(
        library_id=lib.id, filename=file.filename or "import.bib", entry_count=len(entries)
    )
    session.add(bib_file)
    await session.flush()  # assign bib_file.id

    existing = set(
        (
            await session.execute(
                select(Item.citation_key).where(Item.library_id == lib.id)
            )
        ).scalars()
    )
    collisions: list[str] = []
    for entry in entries:
        if entry.citation_key and entry.citation_key in existing:
            collisions.append(entry.citation_key)
        existing.add(entry.citation_key)
        item = Item(
            library_id=lib.id,
            source_file_id=bib_file.id,
            citation_key=entry.citation_key,
            type=entry.csl_type,
            raw_bibtex=entry.raw_bibtex,
        )
        _denormalize(item, entry.csl_json)
        session.add(item)

    await session.commit()
    return ImportResult(
        bib_file_id=bib_file.id,
        filename=bib_file.filename,
        imported=len(entries),
        key_collisions=sorted(set(collisions)),
    )


def _bib_response(items: list[Item], filename: str) -> PlainTextResponse:
    text = build_bibtex([(i.citation_key, i.csl_json, i.raw_bibtex) for i in items])
    return PlainTextResponse(
        text,
        media_type="application/x-bibtex",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/libraries/{library_id}/export.bib")
async def export_library(
    lib: Library = Depends(get_owned_library), session: AsyncSession = Depends(get_session)
):
    rows = await session.execute(
        select(Item).where(Item.library_id == lib.id).order_by(Item.citation_key)
    )
    return _bib_response(list(rows.scalars().all()), f"{lib.name or 'library'}.bib")


@router.get("/bib-files/{bib_file_id}/export.bib")
async def export_bib_file(
    bib_file_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    bib_file = await session.get(BibFile, bib_file_id)
    if bib_file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "BibFile not found")
    lib = await session.get(Library, bib_file.library_id)
    if lib is None or lib.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "BibFile not found")
    rows = await session.execute(
        select(Item)
        .where(Item.source_file_id == bib_file.id)
        .order_by(Item.citation_key)
    )
    return _bib_response(list(rows.scalars().all()), bib_file.filename)
