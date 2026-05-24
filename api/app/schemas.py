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


class LibraryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    description: str
    owner_id: uuid.UUID
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
