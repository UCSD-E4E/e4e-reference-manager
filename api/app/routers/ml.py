"""Phase 3d: LLM-assisted endpoints — tag suggestions (optionally persisted) and
on-demand summaries. All model calls are best-effort and degrade to empty results."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import llm
from ..db import get_session
from ..deps import item_editor, item_viewer
from ..models import Item, Tag
from ..schemas import SuggestedTags, SummaryOut, TagOut

router = APIRouter(tags=["ml"])


@router.get("/items/{item_id}/tags", response_model=list[TagOut])
async def list_item_tags(item: Item = Depends(item_viewer)):
    return item.tags


@router.post("/items/{item_id}/suggest-tags", response_model=SuggestedTags)
async def suggest_tags(
    item: Item = Depends(item_editor),
    apply: bool = Query(False, description="Persist the suggestions as ml-sourced tags"),
    session: AsyncSession = Depends(get_session),
):
    suggestions = await llm.suggest_tags(item.csl_json)
    applied: list[Tag] = []
    if apply and suggestions:
        existing = {t.name.lower() for t in item.tags}
        for name in suggestions:
            if name in existing:
                continue
            tag = (
                await session.execute(
                    select(Tag).where(
                        Tag.library_id == item.library_id, func.lower(Tag.name) == name
                    )
                )
            ).scalars().first()
            if tag is None:
                tag = Tag(library_id=item.library_id, name=name, source="ml")
                session.add(tag)
            item.tags.append(tag)
            existing.add(name)
            applied.append(tag)
        await session.commit()
        for tag in applied:
            await session.refresh(tag)
    return SuggestedTags(suggestions=suggestions, applied=applied)


@router.post("/items/{item_id}/summary", response_model=SummaryOut)
async def summarize_item(item: Item = Depends(item_viewer)):
    summary = await llm.summarize(item.csl_json, item.pdf_text or "")
    return SummaryOut(summary=summary or "")
