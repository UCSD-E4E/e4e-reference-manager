"""Append-only audit logging helpers.

`record` stages an AuditEvent on the session (the caller commits). Snapshots are stored
as JSON in `before`/`after` so history can be displayed and items restored.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditEvent, Item, User


def item_snapshot(item: Item) -> dict:
    return {
        "citation_key": item.citation_key,
        "type": item.type,
        "csl_json": item.csl_json,
        "title": item.title,
        "year": item.year,
        "doi": item.doi,
        "version": item.version,
    }


def record(
    session: AsyncSession,
    *,
    actor: User | None,
    library_id: uuid.UUID | None,
    entity_type: str,
    entity_id: uuid.UUID,
    operation: str,
    summary: str = "",
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    session.add(
        AuditEvent(
            actor_id=actor.id if actor else None,
            library_id=library_id,
            entity_type=entity_type,
            entity_id=entity_id,
            operation=operation,
            summary=summary,
            before=before,
            after=after,
        )
    )
