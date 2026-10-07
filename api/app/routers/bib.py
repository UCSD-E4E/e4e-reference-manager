import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..bibtex import build_bibtex, parse_bibtex, year_from_csl
from ..db import get_session
from ..deps import library_access_level, library_editor, library_viewer
from ..dedupe import match_keys
from ..models import BibFile, Item, Library, User
from ..routers.items import _denormalize
from ..schemas import ImportDuplicate, ImportResult

router = APIRouter(tags=["bibtex"])


@router.post("/libraries/{library_id}/import", response_model=ImportResult)
async def import_bib(
    file: UploadFile,
    lib: Library = Depends(library_editor),
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

    # Duplicate detection (app.dedupe rule): the same paper is matched by DOI, else by
    # normalized title + year, against the project's items and this file's own entries.
    rows = await session.execute(
        select(Item.id, Item.citation_key, Item.doi, Item.title, Item.year).where(
            Item.library_id == lib.id
        )
    )
    keys: set[str] = set()
    owner: dict[tuple[str, object], uuid.UUID] = {}
    for iid, key, doi, title, year in rows:
        keys.add(key)
        for k in match_keys(doi, title, year):
            owner.setdefault(k, iid)

    imported = 0
    duplicates: list[ImportDuplicate] = []
    collisions: list[str] = []
    for entry in entries:
        csl = entry.csl_json
        mkeys = match_keys(csl.get("DOI"), csl.get("title"), year_from_csl(csl))
        hit = next((k for k in mkeys if k in owner), None)
        if hit is not None:
            duplicates.append(
                ImportDuplicate(
                    citation_key=entry.citation_key, item_id=owner[hit], matched_on=hit[0]
                )
            )
            continue

        if entry.citation_key and entry.citation_key in keys:
            collisions.append(entry.citation_key)
        keys.add(entry.citation_key)
        item = Item(
            library_id=lib.id,
            source_file_id=bib_file.id,
            citation_key=entry.citation_key,
            type=entry.csl_type,
            raw_bibtex=entry.raw_bibtex,
        )
        _denormalize(item, entry.csl_json)
        session.add(item)
        await session.flush()  # assign item.id for later duplicates of it
        for k in mkeys:
            owner[k] = item.id
        imported += 1

    await session.commit()
    return ImportResult(
        bib_file_id=bib_file.id,
        filename=bib_file.filename,
        imported=imported,
        duplicates=duplicates,
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
    lib: Library = Depends(library_viewer), session: AsyncSession = Depends(get_session)
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
    if lib is None or await library_access_level(session, user, lib) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "BibFile not found")
    rows = await session.execute(
        select(Item)
        .where(Item.source_file_id == bib_file.id)
        .order_by(Item.citation_key)
    )
    return _bib_response(list(rows.scalars().all()), bib_file.filename)
