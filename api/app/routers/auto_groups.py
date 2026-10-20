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

from .. import embeddings, llm, topics
from ..auth import get_current_user
from ..db import get_session
from ..deps import _rank, library_access_level, library_editor, library_viewer
from ..models import AutoGroup, Item, Library, Tag, User, item_tag
from ..schemas import NAME_MAX, AutoGroupCreate, AutoGroupGenerate, AutoGroupOut
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
        if not isinstance(value, str):  # rules are validated on create; be safe anyway
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
        name = params.get("name")
        source = params.get("source")
        if not isinstance(name, str) or not isinstance(source, str | None):
            return base.where(text("false"))
        name = name.lower()
        q = (
            base.join(item_tag, item_tag.c.item_id == Item.id)
            .join(Tag, Tag.id == item_tag.c.tag_id)
            .where(func.lower(Tag.name) == name)
        )
        if source:
            q = q.where(Tag.source == source)
        return q

    if ag.kind == "search":
        q = params.get("q")
        q = q.strip() if isinstance(q, str) else ""
        if not q:
            return base.where(text("false"))
        tsquery = func.websearch_to_tsquery("english", q)
        return base.where(Item.search_tsv.op("@@")(tsquery))

    return base.where(text("false"))


async def _count(session: AsyncSession, ag: AutoGroup) -> int:
    n = await session.scalar(select(func.count()).select_from(_autogroup_select(ag).subquery()))
    return int(n or 0)


# Author family names per live item, for generating and counting author groups. Matches
# _autogroup_select's `author @> [{"family": v}]`; tolerates a non-array `author`.
_AUTHOR_FAMILIES = (
    "SELECT a->>'family' AS family, count(DISTINCT item.id) AS n "
    "FROM item, jsonb_array_elements(CASE jsonb_typeof(csl_json->'author') "
    "  WHEN 'array' THEN csl_json->'author' ELSE '[]'::jsonb END) AS a "
    "WHERE library_id = :lib AND deleted_at IS NULL AND a->>'family' IS NOT NULL "
    "GROUP BY 1"
)
_JOURNAL = Item.csl_json["container-title"].astext


class _Counter:
    """Member counts for many auto-groups of one library in a handful of grouped queries
    (one per rule kind) instead of one COUNT per group. Search groups, which need a
    full-text match each, are still counted one by one."""

    def __init__(self, session: AsyncSession, lib_id: uuid.UUID):
        self.session = session
        self.lib_id = lib_id
        self._cache: dict[str, dict] = {}

    async def _grouped(self, key: str, *cols) -> dict:
        if key not in self._cache:
            live = (
                select(*cols, func.count(func.distinct(Item.id)))
                .select_from(Item)
                .where(Item.library_id == self.lib_id, Item.deleted_at.is_(None))
            )
            if key.startswith("tag"):
                live = live.join(item_tag, item_tag.c.item_id == Item.id).join(
                    Tag, Tag.id == item_tag.c.tag_id
                )
            rows = await self.session.execute(live.group_by(*cols))
            self._cache[key] = {tuple(r[:-1]): r[-1] for r in rows.all()}
        return self._cache[key]

    async def _authors(self) -> dict:
        if "author" not in self._cache:
            rows = await self.session.execute(text(_AUTHOR_FAMILIES).bindparams(lib=self.lib_id))
            self._cache["author"] = {(r.family,): r.n for r in rows}
        return self._cache["author"]

    async def count(self, ag: AutoGroup) -> int:
        p = ag.params or {}
        value = p.get("value")
        if ag.kind == "field" and isinstance(value, str):
            field = p.get("field")
            if field == "year":
                try:
                    year = int(value)
                except ValueError:
                    return 0
                return (await self._grouped("year", Item.year)).get((year,), 0)
            if field == "type":
                return (await self._grouped("type", Item.type)).get((value,), 0)
            if field == "journal":
                return (await self._grouped("journal", _JOURNAL)).get((value,), 0)
            if field == "author":
                return (await self._authors()).get((value,), 0)
            return 0
        if ag.kind == "tag":
            name, source = p.get("name"), p.get("source")
            if not isinstance(name, str) or not isinstance(source, str | None):
                return 0
            if source is None:
                counts = await self._grouped("tag", func.lower(Tag.name))
                return counts.get((name.lower(),), 0)
            counts = await self._grouped("tag+source", func.lower(Tag.name), Tag.source)
            return counts.get((name.lower(), source), 0)
        if ag.kind == "search":
            return await _count(self.session, ag)
        return 0


async def _hydrate(session: AsyncSession, ags: Iterable[AutoGroup]) -> list[AutoGroupOut]:
    out: list[AutoGroupOut] = []
    counters: dict[uuid.UUID, _Counter] = {}
    for ag in ags:
        counter = counters.setdefault(ag.library_id, _Counter(session, ag.library_id))
        out.append(
            AutoGroupOut(
                id=ag.id,
                library_id=ag.library_id,
                name=ag.name,
                kind=ag.kind,
                params=ag.params or {},
                count=await counter.count(ag),
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

def _rule_key(kind: str, params: dict) -> tuple[str, str]:
    return kind, json.dumps(params, sort_keys=True)


class _Creator:
    """Adds auto-groups to a library, skipping any whose kind+params already exist there.
    The existing rules are loaded once, so generating thousands of groups stays linear."""

    def __init__(self, session: AsyncSession, lib: Library):
        self.session = session
        self.lib = lib
        self.created: list[AutoGroup] = []
        self._seen: set[tuple[str, str]] | None = None

    async def add(self, name: str, kind: str, params: dict) -> None:
        if self._seen is None:
            rows = await self.session.execute(
                select(AutoGroup.kind, AutoGroup.params).where(
                    AutoGroup.library_id == self.lib.id
                )
            )
            self._seen = {_rule_key(k, p or {}) for k, p in rows.all()}
        key = _rule_key(kind, params)
        if key in self._seen:
            return
        self._seen.add(key)
        # Generated from free-text metadata: keep the full value in params, cap the label.
        ag = AutoGroup(library_id=self.lib.id, name=name[:NAME_MAX], kind=kind, params=params)
        self.session.add(ag)
        self.created.append(ag)


async def _generate_field(
    session: AsyncSession, lib: Library, field: str
) -> list[AutoGroup]:
    creator = _Creator(session, lib)
    live = (Item.library_id == lib.id, Item.deleted_at.is_(None))
    if field == "year":
        rows = await session.execute(
            select(Item.year).where(*live, Item.year.is_not(None)).distinct()
        )
        for y in sorted(y for (y,) in rows.all() if y is not None):
            await creator.add(str(y), "field", {"field": "year", "value": str(y)})
    elif field == "type":
        rows = await session.execute(select(Item.type).where(*live).distinct())
        for (t,) in rows.all():
            if t:
                await creator.add(t, "field", {"field": "type", "value": t})
    elif field == "journal":
        rows = await session.execute(
            select(_JOURNAL).where(*live, _JOURNAL.is_not(None)).distinct()
        )
        for (j,) in rows.all():
            if j:
                await creator.add(j, "field", {"field": "journal", "value": j})
    elif field == "author":
        rows = await session.execute(text(_AUTHOR_FAMILIES).bindparams(lib=lib.id))
        for r in rows:
            if r.family:
                await creator.add(r.family, "field", {"field": "author", "value": r.family})
    return creator.created


async def _generate_tags(
    session: AsyncSession, lib: Library, source: str | None, min_items: int = 1
) -> list[AutoGroup]:
    # Tags on at least `min_items` papers outside the trash.
    shared = (
        select(item_tag.c.tag_id)
        .join(Item, Item.id == item_tag.c.item_id)
        .where(Item.deleted_at.is_(None))
        .group_by(item_tag.c.tag_id)
        .having(func.count() >= min_items)
    )
    q = select(Tag).where(Tag.library_id == lib.id, Tag.id.in_(shared))
    if source:
        q = q.where(Tag.source == source)
    rows = await session.execute(q)
    creator = _Creator(session, lib)
    for tag in rows.scalars().all():
        params: dict = {"name": tag.name}
        if source:
            params["source"] = source
        await creator.add(tag.name, "tag", params)
    return creator.created


# Model calls per "generate from ML tags" request. Ollama answers one prompt at a time
# (seconds each on CPU), so a big project is tagged over several clicks rather than in
# one request long enough to time out.
ML_TAG_BATCH = 10
ML_GROUP_MIN_ITEMS = 3


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


async def _generate_topics(session: AsyncSession, lib: Library) -> dict:
    """Cluster the project's papers by embedding and give each paper one topic tag
    (source "topic"), with a live tag auto-group per topic. Replaces earlier topics, so
    rerunning re-fits them to the project as it grows. All-or-nothing: if the model can't
    name every cluster, nothing changes."""
    items = (
        await session.execute(
            select(Item).where(Item.library_id == lib.id).order_by(Item.created_at, Item.id)
        )
    ).scalars().all()
    backfilled = False
    for item in items:  # e.g. imported while Ollama was down
        if item.embedding is None:
            item.embedding = await embeddings.embed_item_csl(item.csl_json)
            backfilled = backfilled or item.embedding is not None
    if backfilled:  # keep them even if naming the topics fails below
        await session.commit()
    embedded = [i for i in items if i.embedding is not None]
    if len(embedded) < topics.MIN_TOPIC_ITEMS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Topics need at least {topics.MIN_TOPIC_ITEMS} papers with embeddings; "
            f"this project has {len(embedded)} (is Ollama running?).",
        )

    k = topics.choose_k(len(embedded))
    labels = topics.cluster([list(i.embedding) for i in embedded], k)
    clusters: dict[int, list[Item]] = {}
    for item, label in zip(embedded, labels):
        clusters.setdefault(label, []).append(item)

    # Clusters the model gives the same name are one topic to a reader: merge them.
    named: dict[str, list[Item]] = {}
    for members in sorted(clusters.values(), key=len, reverse=True):
        reply = await llm.chat(
            topics.topic_prompt([m.title or m.citation_key for m in members]),
            system=topics.TOPIC_SYSTEM,
        )
        name = topics.parse_topic_name(reply)
        if name is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "The model didn't name the topics (is Ollama running?). Nothing was changed.",
            )
        named.setdefault(name, []).extend(members)

    # Replace the previous topics: their groups, then their tags (item links cascade).
    for ag in (
        await session.execute(
            select(AutoGroup).where(AutoGroup.library_id == lib.id, AutoGroup.kind == "tag")
        )
    ).scalars():
        if (ag.params or {}).get("source") == "topic":
            await session.delete(ag)
    for tag in (
        await session.execute(
            select(Tag).where(Tag.library_id == lib.id, Tag.source == "topic")
        )
    ).scalars():
        await session.delete(tag)
    await session.flush()

    for name, members in named.items():
        tag = Tag(library_id=lib.id, name=name, source="topic")
        session.add(tag)
        await session.flush()
        for m in members:
            await session.execute(item_tag.insert().values(item_id=m.id, tag_id=tag.id))
        session.add(
            AutoGroup(
                library_id=lib.id,
                name=name,
                kind="tag",
                params={"name": name, "source": "topic"},
            )
        )
    await session.commit()
    return {
        "created": len(named),
        "clustered": len(embedded),
        "unclustered": len(items) - len(embedded),
    }


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
        # The model's tags are specific; a group only earns its place once 3 papers share it.
        created = await _generate_tags(session, lib, source="ml", min_items=ML_GROUP_MIN_ITEMS)
        await session.commit()
        return {"created": len(created), "tagged": tagged, "remaining": remaining}
    elif src == "topics":
        return await _generate_topics(session, lib)
    elif src == "manual_tags":
        created = await _generate_tags(session, lib, source="manual")
    elif src == "all_tags":
        created = await _generate_tags(session, lib, source=None)
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown source: {src}")
    await session.commit()
    return {"created": len(created)}
