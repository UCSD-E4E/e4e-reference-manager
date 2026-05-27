"""Phase 4: PDF annotations (highlights + comments) anchored to an Attachment.

RBAC mirrors notes: library `view` to read, `edit` to create, author-or-manager to edit
or delete. Mutations are written to the audit log under entity_type "annotation"."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..auth import get_current_user
from ..db import get_session
from ..deps import _rank, library_access_level
from ..models import Annotation, Attachment, Item, Library, User
from ..schemas import AnnotationCreate, AnnotationOut, AnnotationUpdate

router = APIRouter(tags=["annotations"])


async def _attachment_for(
    attachment_id: uuid.UUID, user: User, session: AsyncSession, min_level: str
) -> tuple[Attachment, Item]:
    att = await session.get(Attachment, attachment_id)
    item = await session.get(Item, att.item_id) if att else None
    lib = await session.get(Library, item.library_id) if item else None
    level = await library_access_level(session, user, lib) if lib else None
    if att is None or level is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    if _rank(level) < _rank(min_level):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {min_level} access")
    return att, item


async def _editable_annotation(
    annotation_id: uuid.UUID, user: User, session: AsyncSession
) -> tuple[Annotation, Item]:
    ann = await session.get(Annotation, annotation_id)
    if ann is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Annotation not found")
    att = await session.get(Attachment, ann.attachment_id)
    item = await session.get(Item, att.item_id) if att else None
    lib = await session.get(Library, item.library_id) if item else None
    level = await library_access_level(session, user, lib) if lib else None
    if level is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Annotation not found")
    # author may edit/delete own; library managers may edit/delete any
    if ann.author_id != user.id and _rank(level) < _rank("manage"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the author or a manager can do that")
    return ann, item


@router.get("/attachments/{attachment_id}/annotations", response_model=list[AnnotationOut])
async def list_annotations(
    attachment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    att, _ = await _attachment_for(attachment_id, user, session, "view")
    rows = await session.execute(
        select(Annotation)
        .where(Annotation.attachment_id == att.id)
        .order_by(Annotation.page, Annotation.created_at)
    )
    return list(rows.scalars().all())


@router.post(
    "/attachments/{attachment_id}/annotations", response_model=AnnotationOut, status_code=201
)
async def create_annotation(
    attachment_id: uuid.UUID,
    payload: AnnotationCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    att, item = await _attachment_for(attachment_id, user, session, "edit")
    ann = Annotation(
        attachment_id=att.id,
        author_id=user.id,
        page=payload.page,
        rects=payload.rects,
        color=payload.color,
        quote=payload.quote,
        comment=payload.comment,
    )
    session.add(ann)
    await session.flush()
    snippet = (payload.quote or payload.comment or "")[:40]
    record(
        session,
        actor=user,
        library_id=item.library_id,
        entity_type="annotation",
        entity_id=ann.id,
        operation="create",
        summary=f"Annotated p.{payload.page}" + (f" — “{snippet}”" if snippet else ""),
        after={"page": ann.page, "quote": ann.quote, "comment": ann.comment},
    )
    await session.commit()
    await session.refresh(ann)
    return ann


@router.patch("/annotations/{annotation_id}", response_model=AnnotationOut)
async def update_annotation(
    annotation_id: uuid.UUID,
    payload: AnnotationUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    ann, item = await _editable_annotation(annotation_id, user, session)
    if payload.color is not None:
        ann.color = payload.color
    if payload.comment is not None:
        ann.comment = payload.comment
    if payload.rects is not None:
        ann.rects = payload.rects
    record(
        session,
        actor=user,
        library_id=item.library_id,
        entity_type="annotation",
        entity_id=ann.id,
        operation="update",
        summary="Edited an annotation",
    )
    await session.commit()
    await session.refresh(ann)
    return ann


@router.delete("/annotations/{annotation_id}", status_code=204)
async def delete_annotation(
    annotation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    ann, item = await _editable_annotation(annotation_id, user, session)
    record(
        session,
        actor=user,
        library_id=item.library_id,
        entity_type="annotation",
        entity_id=ann.id,
        operation="delete",
        summary="Deleted an annotation",
        before={"page": ann.page, "quote": ann.quote, "comment": ann.comment},
    )
    await session.delete(ann)
    await session.commit()
