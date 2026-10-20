"""Pydantic request/response schemas."""
from __future__ import annotations

import uuid
from datetime import datetime

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .auth import DEFAULT_TOKEN_DAYS, MAX_TOKEN_DAYS


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    name: str


class ApiTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    expires_in_days: int = Field(default=DEFAULT_TOKEN_DAYS, ge=1, le=MAX_TOKEN_DAYS)


class ApiTokenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime


class ApiTokenCreated(ApiTokenOut):
    """Creation response: the only time the raw token is ever returned."""

    token: str


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



class AttachmentText(BaseModel):
    """A page of an attachment's extracted text (empty for scans and non-PDFs)."""

    text: str
    offset: int
    total_chars: int
    next_offset: int | None  # pass back as `offset` for the next page; None = done


class PdfUploadLinkOut(BaseModel):
    url: str  # POST one PDF here as multipart field `file`; works once
    expires_at: datetime
    curl: str  # ready-to-run upload command (replace paper.pdf with the file's path)


class FetchedPdf(BaseModel):
    attachment: AttachmentOut
    source: str  # unpaywall | arxiv | url
    url: str


class LibraryPdfFetch(BaseModel):
    """One batch of a library-wide open-access PDF fetch."""
    checked: int
    attached: int
    already_had_pdf: int
    not_found: int  # had a DOI/arXiv id, but no open-access copy
    no_identifier: int  # nothing to look up: no DOI, arXiv id, or usable http(s) URL
    failed: int  # storage/extraction error; safe to retry
    next_after: uuid.UUID | None  # pass back as `after` for the next batch; None = done

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
    validation: dict | None = None  # Phase 5 anti-hallucination verdict
    created_at: datetime
    updated_at: datetime
    attachments: list[AttachmentOut] = Field(default_factory=list)


class TrashedItemOut(BaseModel):
    item: ItemOut
    deleted_at: datetime
    deleted_by: UserOut | None
    purge_at: datetime  # when it will be deleted for good


class ItemList(BaseModel):
    total: int
    items: list[ItemOut]


class ImportDuplicate(BaseModel):
    """An entry skipped because the project already has that paper."""

    citation_key: str
    item_id: uuid.UUID  # the existing item (or earlier entry of this file) it matched
    matched_on: str  # doi | title


class PasteIn(BaseModel):
    bibtex: str = Field(max_length=2_000_000)
    indices: list[int] | None = None  # entry positions from the preview; None = all
    filename: str = "pasted.bib"


class PasteDuplicate(BaseModel):
    item_id: uuid.UUID | None = None  # an item already in the project…
    entry_index: int | None = None  # …or an earlier entry of the same paste
    matched_on: str  # doi | title


class PastePreviewEntry(BaseModel):
    index: int
    citation_key: str
    csl_type: str
    title: str
    year: int | None
    doi: str | None
    duplicate: PasteDuplicate | None
    key_collision: bool  # a different paper in the project already uses this key
    validation: dict | None = None  # verdict; None for duplicates (not checked)


class PastePreview(BaseModel):
    entries: list[PastePreviewEntry]


class DedupeGroup(BaseModel):
    kept: uuid.UUID
    merged: list[uuid.UUID]
    matched_on: str  # doi | title


class DedupeResult(BaseModel):
    merged: int
    groups: list[DedupeGroup]


class ImportResult(BaseModel):
    bib_file_id: uuid.UUID
    filename: str
    imported: int
    duplicates: list[ImportDuplicate] = Field(default_factory=list)
    key_collisions: list[str] = Field(default_factory=list)


# --- Collections (Phase 4) ---


# Bounds of the String(n) / INTEGER columns these fields are stored in.
NAME_MAX = 255
COLOR_MAX = 16
INT32_MAX = 2**31 - 1


class CollectionCreate(BaseModel):
    name: str = Field(max_length=NAME_MAX)
    parent_id: uuid.UUID | None = None


class CollectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    library_id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    created_at: datetime


# --- Auto-groups (Phase 7) ---


AUTO_GROUP_FIELDS = ("year", "type", "journal", "author")


def _str_param(params: dict, key: str, *, required: bool = True) -> None:
    value = params.get(key)
    if value is None and not required:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"params.{key} must be a non-empty string")


class AutoGroupCreate(BaseModel):
    name: str = Field(max_length=NAME_MAX)
    kind: Literal["field", "tag", "search"]
    params: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_params(self) -> AutoGroupCreate:
        """Reject rules the membership query can't run, so a broken group is never
        stored (where it would break listing the library's auto-groups)."""
        p = self.params
        if self.kind == "field":
            if p.get("field") not in AUTO_GROUP_FIELDS:
                raise ValueError(f"params.field must be one of {', '.join(AUTO_GROUP_FIELDS)}")
            _str_param(p, "value")
            if p["field"] == "year" and not p["value"].strip().isdigit():
                raise ValueError("params.value must be a year")
        elif self.kind == "tag":
            _str_param(p, "name")
            _str_param(p, "source", required=False)
        else:
            _str_param(p, "q")
            _str_param(p, "mode", required=False)
        return self


class AutoGroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    library_id: uuid.UUID
    name: str
    kind: str
    params: dict
    count: int  # live member count
    created_at: datetime


class AutoGroupGenerate(BaseModel):
    # year | type | journal | author | ml_tags | manual_tags | all_tags
    source: str


# --- Tags / ML (Phase 3d) ---


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    source: str  # manual | ml


class TagsIn(BaseModel):
    tags: list[Annotated[str, Field(max_length=NAME_MAX)]]


class SuggestedTags(BaseModel):
    suggestions: list[str]
    applied: list[TagOut] = Field(default_factory=list)


class SummaryOut(BaseModel):
    summary: str


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


# --- Annotations (Phase 4) ---


class AnnotationCreate(BaseModel):
    page: int = Field(default=1, ge=1, le=INT32_MAX)
    rects: list[dict] = Field(default_factory=list)  # [{x,y,w,h}] normalized 0..1
    color: str = Field(default="#ffd54f", max_length=COLOR_MAX)
    quote: str = ""
    comment: str = ""


class AnnotationUpdate(BaseModel):
    color: str | None = Field(default=None, max_length=COLOR_MAX)
    comment: str | None = None
    rects: list[dict] | None = None


class AnnotationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    attachment_id: uuid.UUID
    author_id: uuid.UUID | None
    page: int
    rects: list[dict]
    color: str
    quote: str
    comment: str
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


# --- Ingestion (Phase 2) ---


class IngestRequest(BaseModel):
    query: str  # DOI / arXiv / PMID / ISBN identifier, or a URL


class IngestResultItem(BaseModel):
    status: str  # created | duplicate
    item_id: uuid.UUID | None
    citation_key: str
    title: str
    doi: str | None = None


class IngestResult(BaseModel):
    results: list[IngestResultItem]


class MetadataProposal(BaseModel):
    csl_json: dict
    applied: bool = False
    item: ItemOut | None = None
