"""Phase 5: anti-hallucination validation endpoints.

Per-item POST runs and stores a single verdict; the library-level POST batches over every
item and returns a summary. RBAC follows the library: edit access is required because
validation writes the verdict onto the item."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from .. import validation
from ..db import get_session
from ..deps import item_editor, library_editor
from ..models import Item, Library

router = APIRouter(tags=["validation"])


async def _validate_and_store(item: Item, session: AsyncSession) -> dict:
    verdict = await validation.validate_item_csl(item.csl_json or {})
    item.validation = verdict
    # JSONB mutation on an existing row isn't always autodetected by SQLAlchemy.
    flag_modified(item, "validation")
    return verdict


@router.post("/items/{item_id}/validate")
async def validate_item(
    item: Item = Depends(item_editor),
    session: AsyncSession = Depends(get_session),
):
    verdict = await _validate_and_store(item, session)
    await session.commit()
    return verdict


@router.post("/libraries/{library_id}/validate")
async def validate_library(
    lib: Library = Depends(library_editor),
    session: AsyncSession = Depends(get_session),
):
    rows = await session.execute(select(Item).where(Item.library_id == lib.id))
    items = list(rows.scalars().all())
    counts = {
        "verified": 0,
        "metadata_mismatch": 0,
        "not_found": 0,
        "unverifiable": 0,
    }
    for item in items:
        verdict = await _validate_and_store(item, session)
        counts[verdict["status"]] = counts.get(verdict["status"], 0) + 1
    await session.commit()
    return {"checked": len(items), **counts}
