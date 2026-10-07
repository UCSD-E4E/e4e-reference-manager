"""Phase 7: JabRef-style auto-groups (live, rule-driven).

Three rule kinds:
- field  -> one group per unique value of year/type/journal/author
- tag    -> items carrying a specific tag (optionally filtered by source=ml|manual)
- search -> items matching an FTS query

Membership is recomputed on demand (`/auto-groups/{id}/items` and `/export.bib`)
rather than materialized. A `generate` endpoint bulk-creates one group per distinct
value (e.g. one per year present in the library) or per ml-suggested Tag, skipping
dupes so the call is idempotent.
"""
import json
import uuid
from typing import Iterable

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import Select, exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import llm
from ..auth import get_current_user
from ..db import get_session
from ..deps import _rank, library_access_level, library_editor, library_viewer
from ..models import AutoGroup, Item, Library, Tag, User, item_tag
from ..schemas import AutoGroupCreate, AutoGroupGenerate, AutoGroupOut
from .bib import _bib_response
from .ml import apply_ml_tags

router = APIRouter(tags=["auto-groups"])


# ---------- rule -> SQL Select[Item] ----------

def _autogroup_select(ag: AutoGroup) -> Select:
    """Build the SELECT that yields the items in this auto-group. Returns a query that
    yields nothing (FALSE) for an unknown / malformed rule, so the API never 500s."""
    base = select(Item).where(Item.library_id == ag.library_id)
    params = ag.params or {}

    if ag.kind == "field":
        field = params.get("field")
        value = params.get("value")
        if value is None:
            return base.where(text("false"))
        if field == "year":
            try:
                return base.where(Item.year == int(value))
            except (TypeError, ValueError):
                return base.where(text("false"))
        if field == "type":
            return base.where(Item.type == value)
        if field == "journal":
            return base.where(
                text("csl_json->>'container-title' = :v").bindparams(v=value)
            )
        if field == "author":
            # Match items whose csl_json.author array contains an object with this family.
            return base.where(
                text("(csl_json->'author') @> (:v)::jsonb").bindparams(
                    v=json.dumps([{"family": value}])
                )
            )
        return base.where(text("false"))

    if ag.kind == "tag":
        name = (params.get("name") or "").lower()
        source = params.get("source")
        q = (
            base.join(item_tag, item_tag.c.item_id == Item.id)
            .join(Tag, Tag.id == item_tag.c.tag_id)
            .where(func.lower(Tag.name) == name)
        )
        if source:
            q = q.where(Tag.source == source)
        return q

    if ag.kind == "search":
        q = (params.get("q") or "").strip()
        if not q:
            return base.where(text("false"))
        tsquery = func.websearch_to_tsquery("english", q)
        return base.where(Item.search_tsv.op("@@")(tsquery))

    return base.where(text("false"))


async def _count(session: AsyncSession, ag: AutoGroup) -> int:
    n = await session.scalar(select(func.count()).select_from(_autogroup_select(ag).subquery()))
    return int(n or 0)


async def _hydrate(session: AsyncSession, ags: Iterable[AutoGroup]) -> list[AutoGroupOut]:
    out: list[AutoGroupOut] = []
    for ag in ags:
        out.append(
            AutoGroupOut(
                id=ag.id,
                library_id=ag.library_id,
                name=ag.name,
                kind=ag.kind,
                params=ag.params or {},
                count=await _count(session, ag),
                created_at=ag.created_at,
            )
        )
    return out


# ---------- access helper ----------

async def _auto_group_for(
    auto_group_id: uuid.UUID, user: User, session: AsyncSession, min_level: str
) -> AutoGroup:
    ag = await session.get(AutoGroup, auto_group_id)
    lib = await session.get(Library, ag.library_id) if ag else None
    level = await library_access_level(session, user, lib) if lib else None
    if ag is None or level is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Auto-group not found")
    if _rank(level) < _rank(min_level):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {min_level} access")
    return ag


# ---------- CRUD ----------

@router.get("/libraries/{library_id}/auto-groups", response_model=list[AutoGroupOut])
async def list_auto_groups(
    lib: Library = Depends(library_viewer), session: AsyncSession = Depends(get_session)
):
    rows = await session.execute(
        select(AutoGroup).where(AutoGroup.library_id == lib.id).order_by(AutoGroup.name)
    )
    return await _hydrate(session, rows.scalars().all())


@router.post("/libraries/{library_id}/auto-groups", response_model=AutoGroupOut, status_code=201)
async def create_auto_group(
    payload: AutoGroupCreate,
    lib: Library = Depends(library_editor),
    session: AsyncSession = Depends(get_session),
):
    if payload.kind not in ("field", "tag", "search"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown kind: {payload.kind}")
    ag = AutoGroup(
        library_id=lib.id, name=payload.name, kind=payload.kind, params=payload.params
    )
    session.add(ag)
    await session.commit()
    await session.refresh(ag)
    return (await _hydrate(session, [ag]))[0]


@router.delete("/auto-groups/{auto_group_id}", status_code=204)
async def delete_auto_group(
    auto_group_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    ag = await _auto_group_for(auto_group_id, user, session, "edit")
    await session.delete(ag)
    await session.commit()


# ---------- live membership + export ----------

@router.get("/auto-groups/{auto_group_id}/items")
async def auto_group_items(
    auto_group_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    ag = await _auto_group_for(auto_group_id, user, session, "view")
    rows = await session.execute(_autogroup_select(ag).order_by(Item.citation_key))
    return [
        {"id": str(it.id), "citation_key": it.citation_key, "title": it.title, "year": it.year}
        for it in rows.scalars().all()
    ]


@router.get("/auto-groups/{auto_group_id}/export.bib")
async def export_auto_group(
    auto_group_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    ag = await _auto_group_for(auto_group_id, user, session, "view")
    rows = await session.execute(_autogroup_select(ag).order_by(Item.citation_key))
    return _bib_response(list(rows.scalars().all()), f"{ag.name or 'auto-group'}.bib")


# ---------- generate ----------

async def _exists(session: AsyncSession, lib_id: uuid.UUID, kind: str, params: dict) -> bool:
    """True if an auto-group with identical kind+params already exists in the library."""
    rows = await session.execute(
        select(AutoGroup.id).where(AutoGroup.library_id == lib_id, AutoGroup.kind == kind)
    )
    for (gid,) in rows.all():
        existing = await session.get(AutoGroup, gid)
        if existing and (existing.params or {}) == params:
            return True
    return False


async def _generate_field(
    session: AsyncSession, lib: Library, field: str
) -> list[AutoGroup]:
    created: list[AutoGroup] = []
    if field == "year":
        rows = await session.execute(
            select(Item.year)
            .where(Item.library_id == lib.id, Item.year.is_not(None))
            .distinct()
        )
        values = sorted({y for (y,) in rows.all() if y is not None})
        for y in values:
            params = {"field": "year", "value": str(y)}
            if await _exists(session, lib.id, "field", params):
                continue
            ag = AutoGroup(library_id=lib.id, name=str(y), kind="field", params=params)
            session.add(ag)
            created.append(ag)
    elif field == "type":
        rows = await session.execute(
            select(Item.type).where(Item.library_id == lib.id).distinct()
        )
        for (t,) in rows.all():
            if not t:
                continue
            params = {"field": "type", "value": t}
            if await _exists(session, lib.id, "field", params):
                continue
            ag = AutoGroup(library_id=lib.id, name=t, kind="field", params=params)
            session.add(ag)
            created.append(ag)
    elif field == "journal":
        rows = await session.execute(
            text(
                "SELECT DISTINCT csl_json->>'container-title' AS j "
                "FROM item WHERE library_id = :lib AND csl_json->>'container-title' IS NOT NULL"
            ).bindparams(lib=lib.id)
        )
        for r in rows:
            j = r.j
            if not j:
                continue
            params = {"field": "journal", "value": j}
            if await _exists(session, lib.id, "field", params):
                continue
            ag = AutoGroup(library_id=lib.id, name=j, kind="field", params=params)
            session.add(ag)
            created.append(ag)
    elif field == "author":
        rows = await session.execute(
            text(
                "SELECT DISTINCT a->>'family' AS family "
                "FROM item, jsonb_array_elements("
                "  coalesce(csl_json->'author', '[]'::jsonb)) AS a "
                "WHERE library_id = :lib AND a->>'family' IS NOT NULL"
            ).bindparams(lib=lib.id)
        )
        for r in rows:
            fam = r.family
            if not fam:
                continue
            params = {"field": "author", "value": fam}
            if await _exists(session, lib.id, "field", params):
                continue
            ag = AutoGroup(library_id=lib.id, name=fam, kind="field", params=params)
            session.add(ag)
            created.append(ag)
    return created


async def _generate_tags(
    session: AsyncSession, lib: Library, source: str | None
) -> list[AutoGroup]:
    q = select(Tag).where(Tag.library_id == lib.id)
    if source:
        q = q.where(Tag.source == source)
    rows = await session.execute(q)
    created: list[AutoGroup] = []
    for tag in rows.scalars().all():
        params: dict = {"name": tag.name}
        if source:
            params["source"] = source
        if await _exists(session, lib.id, "tag", params):
            continue
        ag = AutoGroup(library_id=lib.id, name=tag.name, kind="tag", params=params)
        session.add(ag)
        created.append(ag)
    return created


# Model calls per "generate from ML tags" request. Ollama answers one prompt at a time
# (seconds each on CPU), so a big project is tagged over several clicks rather than in
# one request long enough to time out.
ML_TAG_BATCH = 10


async def _ml_tag_untagged(session: AsyncSession, lib: Library) -> tuple[int, int]:
    """Ask the model for tags on (up to ML_TAG_BATCH) items that have no ML tags yet.
    Returns (items tagged, items still without ML tags)."""
    has_ml_tag = exists(
        select(item_tag.c.item_id)
        .join(Tag, Tag.id == item_tag.c.tag_id)
        .where(item_tag.c.item_id == Item.id, Tag.source == "ml")
    )
    untagged = select(Item).where(Item.library_id == lib.id, ~has_ml_tag)
    total = await session.scalar(select(func.count()).select_from(untagged.subquery())) or 0
    # Random order: items the model has nothing to say about can't monopolise every batch.
    batch = (
        await session.execute(untagged.order_by(func.random()).limit(ML_TAG_BATCH))
    ).scalars().all()
    tagged = 0
    for item in batch:
        names = await llm.suggest_tags(item.csl_json)
        if not names:
            # Usually the model is down or timing out (up to 2 min per call); don't wait
            # out the rest of the batch. Nothing is lost — the item stays untagged.
            break
        await apply_ml_tags(session, item, names)
        await session.flush()  # so the next item reuses tags created for this one
        tagged += 1
    return tagged, total - tagged


@router.post("/libraries/{library_id}/auto-groups/generate")
async def generate_auto_groups(
    payload: AutoGroupGenerate,
    lib: Library = Depends(library_editor),
    session: AsyncSession = Depends(get_session),
):
    src = payload.source
    if src in ("year", "type", "journal", "author"):
        created = await _generate_field(session, lib, src)
    elif src == "ml_tags":
        tagged, remaining = await _ml_tag_untagged(session, lib)
        created = await _generate_tags(session, lib, source="ml")
        await session.commit()
        return {"created": len(created), "tagged": tagged, "remaining": remaining}
    elif src == "manual_tags":
        created = await _generate_tags(session, lib, source="manual")
    elif src == "all_tags":
        created = await _generate_tags(session, lib, source=None)
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown source: {src}")
    await session.commit()
    return {"created": len(created)}
