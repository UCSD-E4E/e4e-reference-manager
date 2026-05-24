"""Pydantic request/response schemas."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    name: str


class LibraryCreate(BaseModel):
    name: str
    description: str = ""
    owner_group_id: uuid.UUID | None = None  # if set, library is group-owned


class LibraryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    description: str
    owner_id: uuid.UUID | None
    owner_group_id: uuid.UUID | None
    my_access: str | None = None  # caller's access level on this library
    created_at: datetime
    updated_at: datetime


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    filename: str
    content_type: str
    size: int
    created_at: datetime


class ItemCreate(BaseModel):
    citation_key: str = ""
    type: str = "document"
    csl_json: dict = Field(default_factory=dict)


class ItemUpdate(BaseModel):
    citation_key: str | None = None
    type: str | None = None
    csl_json: dict | None = None
    version: int  # required for optimistic locking


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    library_id: uuid.UUID
    source_file_id: uuid.UUID | None
    citation_key: str
    type: str
    csl_json: dict
    title: str
    year: int | None
    doi: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    attachments: list[AttachmentOut] = Field(default_factory=list)


class ItemList(BaseModel):
    total: int
    items: list[ItemOut]


class ImportResult(BaseModel):
    bib_file_id: uuid.UUID
    filename: str
    imported: int
    key_collisions: list[str] = Field(default_factory=list)


# --- Collaboration: groups, members, shares ---


class GroupCreate(BaseModel):
    slug: str
    name: str
    description: str = ""


class GroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    slug: str
    name: str
    description: str


class MemberAdd(BaseModel):
    email: str


class ShareIn(BaseModel):
    group_id: uuid.UUID
    access_level: str = "view"


class ShareOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    group_id: uuid.UUID
    access_level: str
    created_at: datetime


# --- Notes ---


class NoteCreate(BaseModel):
    body: str


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    item_id: uuid.UUID
    author_id: uuid.UUID | None
    body: str
    created_at: datetime
    updated_at: datetime


# --- Audit / history ---


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    actor_id: uuid.UUID | None
    library_id: uuid.UUID | None
    entity_type: str
    entity_id: uuid.UUID
    operation: str
    summary: str
    occurred_at: datetime
