import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from .. import validation
from ..bibtex import ParsedEntry, build_bibtex, parse_bibtex, year_from_csl
from ..db import get_session
from ..deps import library_access_level, library_editor, library_viewer
from ..dedupe import match_keys
from ..models import BibFile, Item, Library, User
from ..routers.items import _denormalize
from ..schemas import (
    ImportDuplicate,
    ImportResult,
    PasteDuplicate,
    PasteIn,
    PastePreview,
    PastePreviewEntry,
)

router = APIRouter(tags=["bibtex"])


# Paste previews validate each entry against Crossref/arXiv; cap the work per request.
MAX_PASTE_ENTRIES = 200
VALIDATE_CONCURRENCY = 4


async def _project_index(
    session: AsyncSession, lib: Library
) -> tuple[set[str], dict[tuple[str, object], uuid.UUID]]:
    """The project's citation keys, and its items by duplicate-match key (app.dedupe)."""
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
    return keys, owner


def _entry_match_keys(entry: ParsedEntry) -> list[tuple[str, object]]:
    csl = entry.csl_json
    return match_keys(csl.get("DOI"), csl.get("title"), year_from_csl(csl))


def _parse_or_400(raw: str) -> list[ParsedEntry]:
    entries = parse_bibtex(raw)
    if not entries:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No BibTeX entries found")
    return entries


async def _import_entries(
    session: AsyncSession,
    lib: Library,
    entries: list[ParsedEntry],
    filename: str,
    *,
    validate: bool = False,
) -> ImportResult:
    """Add `entries` to the project as one BibFile, skipping duplicates (DOI, else
    normalized title + year) of its items or of earlier entries. With `validate`, each
    added item gets its anti-hallucination verdict (cached from a preview if fresh)."""
    bib_file = BibFile(library_id=lib.id, filename=filename, entry_count=len(entries))
    session.add(bib_file)
    await session.flush()  # assign bib_file.id

    keys, owner = await _project_index(session, lib)
    imported: list[tuple[Item, dict]] = []
    duplicates: list[ImportDuplicate] = []
    collisions: list[str] = []
    for entry in entries:
        mkeys = _entry_match_keys(entry)
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
        imported.append((item, entry.csl_json))

    if validate:
        verdicts = await _validate_all([csl for _, csl in imported])
        for (item, _), verdict in zip(imported, verdicts):
            item.validation = verdict

    await session.commit()
    return ImportResult(
        bib_file_id=bib_file.id,
        filename=bib_file.filename,
        imported=len(imported),
        duplicates=duplicates,
        key_collisions=sorted(set(collisions)),
    )


async def _validate_all(csls: list[dict]) -> list[dict]:
    """Validate several references a few at a time (Crossref's polite pool)."""
    sem = asyncio.Semaphore(VALIDATE_CONCURRENCY)

    async def one(csl: dict) -> dict:
        async with sem:
            return await validation.cached_validate_csl(csl)

    return list(await asyncio.gather(*(one(c) for c in csls)))


@router.post("/libraries/{library_id}/import", response_model=ImportResult)
async def import_bib(
    file: UploadFile,
    lib: Library = Depends(library_editor),
    session: AsyncSession = Depends(get_session),
):
    raw = (await file.read()).decode("utf-8", errors="replace")
    entries = _parse_or_400(raw)
    return await _import_entries(session, lib, entries, file.filename or "import.bib")


@router.post("/libraries/{library_id}/import/preview", response_model=PastePreview)
async def preview_pasted_bibtex(
    payload: PasteIn,
    lib: Library = Depends(library_editor),
    session: AsyncSession = Depends(get_session),
):
    """Parse pasted BibTeX, flag duplicates and key collisions, and validate every
    non-duplicate entry against Crossref/arXiv. Saves nothing."""
    entries = _parse_or_400(payload.bibtex)
    if len(entries) > MAX_PASTE_ENTRIES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Paste at most {MAX_PASTE_ENTRIES} entries at a time ({len(entries)} found); "
            "use Import .bib for larger files.",
        )

    keys, owner = await _project_index(session, lib)
    seen: dict[tuple[str, object], int] = {}
    rows: list[PastePreviewEntry] = []
    for i, entry in enumerate(entries):
        mkeys = _entry_match_keys(entry)
        duplicate = None
        if hit := next((k for k in mkeys if k in owner), None):
            duplicate = PasteDuplicate(item_id=owner[hit], matched_on=hit[0])
        elif hit := next((k for k in mkeys if k in seen), None):
            duplicate = PasteDuplicate(entry_index=seen[hit], matched_on=hit[0])
        else:
            for k in mkeys:
                seen.setdefault(k, i)
        csl = entry.csl_json
        rows.append(
            PastePreviewEntry(
                index=i,
                citation_key=entry.citation_key,
                csl_type=entry.csl_type,
                title=(csl.get("title") or "").strip(),
                year=year_from_csl(csl),
                doi=csl.get("DOI"),
                duplicate=duplicate,
                key_collision=duplicate is None and entry.citation_key in keys,
            )
        )

    to_check = [r for r in rows if r.duplicate is None]
    verdicts = await _validate_all([entries[r.index].csl_json for r in to_check])
    for row, verdict in zip(to_check, verdicts):
        row.validation = verdict
    return PastePreview(entries=rows)


@router.post("/libraries/{library_id}/import/text", response_model=ImportResult)
async def import_pasted_bibtex(
    payload: PasteIn,
    lib: Library = Depends(library_editor),
    session: AsyncSession = Depends(get_session),
):
    """Add pasted BibTeX (only the entries at `indices`, if given — positions from the
    preview), validating each added item. Duplicates are still skipped."""
    entries = _parse_or_400(payload.bibtex)
    if payload.indices is not None:
        wanted = set(payload.indices)
        entries = [e for i, e in enumerate(entries) if i in wanted]
        if not entries:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "No entries selected")
    return await _import_entries(session, lib, entries, payload.filename, validate=True)


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
