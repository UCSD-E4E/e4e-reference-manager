"""Phase 3c: search endpoints — keyword (FTS), semantic (pgvector), and hybrid (RRF),
plus a reindex action to (re)compute embeddings for a library."""
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import embeddings
from ..db import get_session
from ..deps import library_editor, library_viewer
from ..models import Item, Library
from ..schemas import ItemList

router = APIRouter(tags=["search"])

_RRF_K = 60  # reciprocal-rank-fusion constant (standard default)


async def _keyword_ids(session, lib_id, q, limit) -> list[uuid.UUID]:
    tsquery = func.websearch_to_tsquery("english", q)
    stmt = (
        select(Item.id)
        .where(Item.library_id == lib_id, Item.search_tsv.op("@@")(tsquery))
        .order_by(func.ts_rank(Item.search_tsv, tsquery).desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def _semantic_ids(session, lib_id, q, limit) -> list[uuid.UUID]:
    vec = await embeddings.embed_text(q)
    if vec is None:  # Ollama unavailable / empty query -> no semantic results
        return []
    stmt = (
        select(Item.id)
        .where(Item.library_id == lib_id, Item.embedding.is_not(None))
        .order_by(Item.embedding.cosine_distance(vec))
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


def _rrf(*ranked_lists: list[uuid.UUID]) -> list[uuid.UUID]:
    """Fuse ranked id lists by reciprocal rank fusion."""
    scores: dict[uuid.UUID, float] = {}
    for ids in ranked_lists:
        for rank, iid in enumerate(ids):
            scores[iid] = scores.get(iid, 0.0) + 1.0 / (_RRF_K + rank + 1)
    return sorted(scores, key=lambda i: scores[i], reverse=True)


@router.get("/libraries/{library_id}/search", response_model=ItemList)
async def search_library(
    lib: Library = Depends(library_viewer),
    session: AsyncSession = Depends(get_session),
    q: str = Query(..., min_length=1, description="Search query"),
    mode: str = Query("hybrid", pattern="^(keyword|semantic|hybrid)$"),
    limit: int = Query(50, ge=1, le=200),
):
    if mode == "keyword":
        ids = await _keyword_ids(session, lib.id, q, limit)
    elif mode == "semantic":
        ids = await _semantic_ids(session, lib.id, q, limit)
    else:  # hybrid
        kw = await _keyword_ids(session, lib.id, q, limit)
        sem = await _semantic_ids(session, lib.id, q, limit)
        ids = _rrf(kw, sem)[:limit]

    if not ids:
        return ItemList(total=0, items=[])
    rows = await session.execute(select(Item).where(Item.id.in_(ids)))
    by_id = {it.id: it for it in rows.scalars().all()}
    items = [by_id[i] for i in ids if i in by_id]  # preserve ranked order
    return ItemList(total=len(items), items=items)


REINDEX_COMMIT_EVERY = 25


@router.post("/libraries/{library_id}/reindex")
async def reindex_library(
    lib: Library = Depends(library_editor),
    session: AsyncSession = Depends(get_session),
    force: bool = Query(False, description="Re-embed every item, not just those missing one"),
):
    """Compute embeddings for the library's items that have none (e.g. imported while
    Ollama was down); `force` recomputes all of them (e.g. after changing the model).
    Items whose embedding can't be produced are skipped. Progress is committed in
    batches, so an interrupted run keeps what it did."""
    stmt = select(Item).where(Item.library_id == lib.id)
    if not force:
        stmt = stmt.where(Item.embedding.is_(None))
    items = list((await session.execute(stmt.order_by(Item.id))).scalars().all())
    embedded = 0
    for n, item in enumerate(items, 1):
        vec = await embeddings.embed_item_csl(item.csl_json)
        if vec is not None:
            item.embedding = vec
            embedded += 1
        if n % REINDEX_COMMIT_EVERY == 0:
            await session.commit()
    await session.commit()
    return {"items": len(items), "embedded": embedded}
