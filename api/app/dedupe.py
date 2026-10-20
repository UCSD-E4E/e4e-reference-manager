"""Duplicate detection and merging within a project.

One rule everywhere (bib import, identifier import, "merge duplicates"): two items are the
same paper when their normalized DOIs match, or — failing that — their normalized titles
and (known) years do, unless both have DOIs and those differ: a DOI names one paper, so
two "Editorial"s of 2020 with different DOIs are different papers. `DuplicateIndex` is
the only implementation of the rule.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from .bibtex import year_from_csl
from .ingest import normalize_doi, normalize_title
from .models import Item, Note, item_collection, item_tag
from .routers.attachments import _refresh_item_pdf_text
from .routers.items import apply_changes


class DuplicateIndex:
    """Records (each item, import entry, ...) by duplicate-match key, mapped to an owner
    (an item id, an entry index...). Several records may share an owner (a merged
    group); the owner's DOI is the first DOI any of them had, and a title match against
    an owner whose DOI differs from the candidate's is refused."""

    def __init__(self) -> None:
        self._by_doi: dict[str, object] = {}
        self._by_title: dict[tuple[str, int], list[object]] = {}
        self._owner_doi: dict[object, str | None] = {}

    @staticmethod
    def _keys(
        doi: str | None, title: str | None, year: int | None
    ) -> tuple[str | None, tuple[str, int] | None]:
        nt = normalize_title(title)
        return normalize_doi(doi), (nt, year) if nt and year is not None else None

    def find(
        self, doi: str | None, title: str | None, year: int | None
    ) -> tuple[str, object] | None:
        """The owner of an existing record that is the same paper, as ("doi"|"title",
        owner), or None."""
        nd, tk = self._keys(doi, title, year)
        if nd and nd in self._by_doi:
            return "doi", self._by_doi[nd]
        for owner in self._by_title.get(tk, ()) if tk else ():
            od = self._owner_doi[owner]
            if not (nd and od and nd != od):
                return "title", owner
        return None

    def add(self, doi: str | None, title: str | None, year: int | None, owner: object) -> None:
        nd, tk = self._keys(doi, title, year)
        if self._owner_doi.get(owner) is None:
            self._owner_doi[owner] = nd
        if nd:
            self._by_doi.setdefault(nd, owner)
        if tk:
            owners = self._by_title.setdefault(tk, [])
            if owner not in owners:
                owners.append(owner)

    def find_csl(self, csl: dict) -> tuple[str, object] | None:
        return self.find(csl.get("DOI"), csl.get("title"), year_from_csl(csl))

    def add_csl(self, csl: dict, owner: object) -> None:
        self.add(csl.get("DOI"), csl.get("title"), year_from_csl(csl), owner)


async def library_index(
    session: AsyncSession, library_id: uuid.UUID
) -> tuple[set[str], DuplicateIndex]:
    """A library's citation keys, and its items (owned by their ids) for duplicate checks."""
    rows = await session.execute(
        select(Item.id, Item.citation_key, Item.doi, Item.title, Item.year).where(
            Item.library_id == library_id
        )
    )
    keys: set[str] = set()
    index = DuplicateIndex()
    for iid, key, doi, title, year in rows:
        keys.add(key)
        index.add(doi, title, year, iid)
    return keys, index


def find_duplicate_groups(items: Iterable[Item]) -> list[dict]:
    """Group duplicates among `items` (pass them oldest first; the first of each group is
    kept). Returns [{"kept": id, "merged": [ids], "matched_on": "doi"|"title"}]."""
    index = DuplicateIndex()
    groups: dict[uuid.UUID, dict] = {}
    for item in items:
        hit = index.find(item.doi, item.title, item.year)
        if hit is None:
            index.add(item.doi, item.title, item.year, item.id)
            continue
        kind, kept = hit
        group = groups.setdefault(kept, {"kept": kept, "merged": [], "matched_on": kind})
        group["merged"].append(item.id)
        # A later copy may match this one on a key the kept item lacks — and once merged,
        # the group has this copy's DOI if the kept item had none.
        index.add(item.doi, item.title, item.year, kept)
    return list(groups.values())


async def merge_into(session: AsyncSession, keep: Item, other: Item) -> None:
    """Fold `other` into `keep` and delete it: PDFs (with their annotations), notes, tags
    and collection memberships move over, and CSL fields `keep` lacks are filled from
    `other` (keep's own values win). The caller records the audit event and commits."""
    # Move PDFs through the relationship: merely repointing item_id would leave them in
    # other.attachments, whose delete-orphan cascade deletes them along with `other`.
    for att in list(other.attachments):
        other.attachments.remove(att)
        keep.attachments.append(att)
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
    await _refresh_item_pdf_text(session, keep)  # the moved PDFs' text is now keep's
    await session.flush()
