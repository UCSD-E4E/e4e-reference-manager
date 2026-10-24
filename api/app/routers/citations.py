"""Citation graph: which papers in a library cite which.

Links come from two sources and are matched to the library's own papers:
- OpenAlex: a paper's reference list by DOI, matched by OpenAlex work id;
- the paper's attached PDF: GROBID's reference extraction (cached on the attachment),
  matched by DOI or title + year, the same rule as duplicate detection (app.dedupe).

Building is batched (like Find PDFs) because extracting references from PDFs takes a
few seconds each; the graph itself is served from the stored links.
"""
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import grobid, openalex
from ..bibtex import authors_text
from ..db import get_session
from ..dedupe import library_index
from ..deps import library_editor, library_viewer
from ..ingest import normalize_doi
from ..models import Citation, Item, Library
from ..schemas import CitationBuild, CitationEdge, CitationGraph, CitationNode
from ..storage import download_bytes
from .attachments import _is_pdf

router = APIRouter(tags=["citations"])

OPENALEX, PDF = "openalex", "pdf"


async def _resolve_openalex_ids(session: AsyncSession, lib: Library) -> None:
    """Give every DOI'd paper in the library its OpenAlex id (so references can be
    matched to it). Papers OpenAlex doesn't know stay None."""
    rows = await session.execute(
        select(Item.id, Item.doi).where(
            Item.library_id == lib.id, Item.doi.is_not(None), Item.openalex_id.is_(None)
        )
    )
    missing = {iid: normalize_doi(doi) for iid, doi in rows if normalize_doi(doi)}
    if not missing:
        return
    works = await openalex.works_by_doi(missing.values())
    for iid, doi in missing.items():
        if doi in works:
            await session.execute(
                update(Item).where(Item.id == iid).values(openalex_id=works[doi][0])
            )


async def _pdf_references(session: AsyncSession, item: Item) -> list[dict]:
    """References from the item's PDFs (GROBID), extracting each PDF at most once."""
    refs: list[dict] = []
    for att in item.attachments:
        if not _is_pdf(att):
            continue
        if att.references is None:
            data = await run_in_threadpool(download_bytes, att.storage_key)
            att.references = await grobid.extract_references_strict(data)
        refs.extend(r for r in att.references if isinstance(r, dict))
    return refs


@router.post("/libraries/{library_id}/citations/build", response_model=CitationBuild)
async def build_citations(
    after: uuid.UUID | None = None,
    limit: int = Query(20, ge=1, le=100),
    lib: Library = Depends(library_editor),
    session: AsyncSession = Depends(get_session),
):
    """Compute the outgoing citations of one batch of the library's papers (ordered by
    id, after the `after` cursor). Re-running replaces a paper's links; if a lookup
    fails, that source's existing links for the paper are kept."""
    failed = 0
    openalex_ok = True
    if after is None:  # once per build: make every paper matchable by its OpenAlex id
        try:
            await _resolve_openalex_ids(session, lib)
        except openalex.OpenAlexUnavailable:
            openalex_ok = False

    query = select(Item).where(Item.library_id == lib.id).order_by(Item.id).limit(limit)
    if after is not None:
        query = query.where(Item.id > after)
    items = list((await session.execute(query)).scalars().all())
    next_after = items[-1].id if len(items) == limit else None

    by_openalex = dict(
        (
            await session.execute(
                select(Item.openalex_id, Item.id).where(
                    Item.library_id == lib.id, Item.openalex_id.is_not(None)
                )
            )
        ).all()
    )
    _, index = await library_index(session, lib.id)

    works: dict[str, tuple[str, list[str]]] = {}
    batch_dois = [d for d in (normalize_doi(i.doi) for i in items) if d]
    if batch_dois and openalex_ok:
        try:
            works = await openalex.works_by_doi(batch_dois)
        except openalex.OpenAlexUnavailable:
            openalex_ok = False

    linked = links = 0
    now = datetime.now(timezone.utc)
    for item in items:
        found: dict[str, set[uuid.UUID]] = {}  # source -> cited ids, for sources that ran
        doi = normalize_doi(item.doi)
        if doi:
            if openalex_ok:
                oa_id, refs = works.get(doi, (None, []))
                if oa_id and not item.openalex_id:
                    item.openalex_id = oa_id
                found[OPENALEX] = {by_openalex[w] for w in refs if w in by_openalex}
            else:
                failed += 1
        if any(_is_pdf(a) for a in item.attachments):
            try:
                refs = await _pdf_references(session, item)
            except (grobid.GrobidUnavailable, BotoCoreError, ClientError):  # GROBID, S3
                failed += 1
            else:
                found[PDF] = {
                    hit[1] for ref in refs if (hit := index.find_csl(ref)) is not None
                }
        cited_ids = set()
        for source, cited in found.items():
            cited.discard(item.id)  # a paper doesn't cite itself
            await session.execute(
                delete(Citation).where(Citation.citing_id == item.id, Citation.source == source)
            )
            for cited_id in cited:
                session.add(Citation(citing_id=item.id, cited_id=cited_id, source=source))
            cited_ids |= cited
        if found or (not doi and not item.attachments):
            item.citations_checked_at = now
        if cited_ids:
            linked += 1
            links += len(cited_ids)
    await session.commit()
    return CitationBuild(
        checked=len(items), linked=linked, links=links, failed=failed, next_after=next_after
    )


@router.get("/libraries/{library_id}/citations", response_model=CitationGraph)
async def citation_graph(
    lib: Library = Depends(library_viewer), session: AsyncSession = Depends(get_session)
):
    """Every paper in the library as a node, and the citation links between them."""
    items = list(
        (await session.execute(select(Item).where(Item.library_id == lib.id))).scalars()
    )
    ids = {i.id for i in items}
    rows = await session.execute(
        select(Citation.citing_id, Citation.cited_id, Citation.source).where(
            Citation.citing_id.in_(ids) if ids else False
        )
    )
    pairs: dict[tuple[uuid.UUID, uuid.UUID], set[str]] = defaultdict(set)
    for citing, cited, source in rows:
        if cited in ids:  # skips papers in the trash
            pairs[(citing, cited)].add(source)
    cites: dict[uuid.UUID, int] = defaultdict(int)
    cited_by: dict[uuid.UUID, int] = defaultdict(int)
    for citing, cited in pairs:
        cites[citing] += 1
        cited_by[cited] += 1
    checked = await session.scalar(
        select(func.count()).select_from(Item).where(
            Item.library_id == lib.id, Item.citations_checked_at.is_not(None)
        )
    )
    return CitationGraph(
        nodes=[
            CitationNode(
                id=i.id,
                citation_key=i.citation_key,
                title=i.title,
                year=i.year,
                authors=authors_text(i.csl_json or {}),
                has_pdf=any(_is_pdf(a) for a in i.attachments),
                cites=cites[i.id],
                cited_by=cited_by[i.id],
            )
            for i in sorted(items, key=lambda i: i.citation_key)
        ],
        edges=[
            CitationEdge(source=s, target=t, sources=sorted(src))
            for (s, t), src in sorted(pairs.items())
        ],
        checked=checked or 0,
        total=len(items),
    )
