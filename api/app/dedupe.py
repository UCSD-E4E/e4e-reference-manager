"""Duplicate detection and merging within a project.

One rule everywhere (bib import, identifier import, "merge duplicates"): two items are the
same paper when their normalized DOIs match, or — failing that — their normalized titles
and years do.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from .bibtex import year_from_csl
from .ingest import normalize_doi, normalize_title
from .models import Item, Note, item_collection, item_tag
from .routers.items import apply_changes


def match_keys(doi: str | None, title: str | None, year: int | None) -> list[tuple[str, object]]:
    """The keys an item can be matched on, strongest first: ("doi", d), ("title", (t, y))."""
    keys: list[tuple[str, object]] = []
    if nd := normalize_doi(doi):
        keys.append(("doi", nd))
    if nt := normalize_title(title):
        keys.append(("title", (nt, year)))
    return keys


def csl_match_keys(csl: dict) -> list[tuple[str, object]]:
    return match_keys(csl.get("DOI"), csl.get("title"), year_from_csl(csl))


async def library_index(
    session: AsyncSession, library_id: uuid.UUID
) -> tuple[set[str], dict[tuple[str, object], uuid.UUID]]:
    """A library's citation keys, and its items by duplicate-match key."""
    rows = await session.execute(
        select(Item.id, Item.citation_key, Item.doi, Item.title, Item.year).where(
            Item.library_id == library_id
        )
    )
    keys: set[str] = set()
    owner: dict[tuple[str, object], uuid.UUID] = {}
    for iid, key, doi, title, year in rows:
        keys.add(key)
        for k in match_keys(doi, title, year):
            owner.setdefault(k, iid)
    return keys, owner


def find_duplicate_groups(items: Iterable[Item]) -> list[dict]:
    """Group duplicates among `items` (pass them oldest first; the first of each group is
    kept). Returns [{"kept": id, "merged": [ids], "matched_on": "doi"|"title"}]."""
    owner: dict[tuple[str, object], uuid.UUID] = {}
    groups: dict[uuid.UUID, dict] = {}
    for item in items:
        keys = match_keys(item.doi, item.title, item.year)
        hit = next(((k[0], owner[k]) for k in keys if k in owner), None)
        if hit is None:
            for k in keys:
                owner[k] = item.id
            continue
        kind, kept = hit
        group = groups.setdefault(kept, {"kept": kept, "merged": [], "matched_on": kind})
        group["merged"].append(item.id)
        for k in keys:  # a later copy may match this one on a key the kept item lacks
            owner.setdefault(k, kept)
    return list(groups.values())


async def merge_into(session: AsyncSession, keep: Item, other: Item) -> None:
    """Fold `other` into `keep` and delete it: PDFs (with their annotations), notes, tags
    and collection memberships move over, and CSL fields `keep` lacks are filled from
    `other` (keep's own values win). The caller records the audit event and commits."""
    for att in list(other.attachments):
        att.item_id = keep.id
    notes = (await session.execute(select(Note).where(Note.item_id == other.id))).scalars()
    for note in notes:
        note.item_id = keep.id

    links = ((item_tag, item_tag.c.tag_id), (item_collection, item_collection.c.collection_id))
    for table, col in links:
        mine = set((await session.execute(select(col).where(table.c.item_id == keep.id))).scalars())
        theirs = set(
            (await session.execute(select(col).where(table.c.item_id == other.id))).scalars()
        )
        for ref in theirs - mine:
            await session.execute(insert(table).values({"item_id": keep.id, col.key: ref}))

    merged = dict(keep.csl_json or {})
    for k, v in (other.csl_json or {}).items():
        if v and not merged.get(k):
            merged[k] = v
    if apply_changes(keep, csl=merged):
        keep.version += 1

    await session.delete(other)
    await session.flush()
