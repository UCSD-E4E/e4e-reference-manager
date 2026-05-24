import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..auth import get_current_user
from ..db import get_session
from ..deps import _rank, item_editor, item_viewer, library_access_level
from ..models import Item, Library, Note, User
from ..schemas import NoteCreate, NoteOut

router = APIRouter(tags=["notes"])


@router.get("/items/{item_id}/notes", response_model=list[NoteOut])
async def list_notes(
    item: Item = Depends(item_viewer), session: AsyncSession = Depends(get_session)
):
    rows = await session.execute(
        select(Note).where(Note.item_id == item.id).order_by(Note.created_at)
    )
    return list(rows.scalars().all())


@router.post("/items/{item_id}/notes", response_model=NoteOut, status_code=201)
async def create_note(
    payload: NoteCreate,
    item: Item = Depends(item_editor),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    note = Note(item_id=item.id, author_id=user.id, body=payload.body)
    session.add(note)
    await session.flush()
    record(
        session,
        actor=user,
        library_id=item.library_id,
        entity_type="note",
        entity_id=note.id,
        operation="create",
        summary="Added a note",
        after={"body": note.body},
    )
    await session.commit()
    await session.refresh(note)
    return note


async def _editable_note(
    note_id: uuid.UUID, user: User, session: AsyncSession
) -> tuple[Note, Item]:
    note = await session.get(Note, note_id)
    if note is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Note not found")
    item = await session.get(Item, note.item_id)
    lib = await session.get(Library, item.library_id) if item else None
    level = await library_access_level(session, user, lib) if lib else None
    if level is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Note not found")
    # author may edit/delete own; library managers may edit/delete any
    if note.author_id != user.id and _rank(level) < _rank("manage"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the author or a manager can do that")
    return note, item


@router.patch("/notes/{note_id}", response_model=NoteOut)
async def update_note(
    note_id: uuid.UUID,
    payload: NoteCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    note, item = await _editable_note(note_id, user, session)
    before = {"body": note.body}
    note.body = payload.body
    record(
        session,
        actor=user,
        library_id=item.library_id,
        entity_type="note",
        entity_id=note.id,
        operation="update",
        summary="Edited a note",
        before=before,
        after={"body": note.body},
    )
    await session.commit()
    await session.refresh(note)
    return note


@router.delete("/notes/{note_id}", status_code=204)
async def delete_note(
    note_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    note, item = await _editable_note(note_id, user, session)
    record(
        session,
        actor=user,
        library_id=item.library_id,
        entity_type="note",
        entity_id=note.id,
        operation="delete",
        summary="Deleted a note",
        before={"body": note.body},
    )
    await session.delete(note)
    await session.commit()
