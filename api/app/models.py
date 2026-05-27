"""SQLAlchemy ORM models.

Phase 0: User, Library (= Project), Collection, BibFile, Item (= Reference),
Attachment, Tag.
Phase 1 (collaboration + history): Group, user_group, group/shared Library
ownership, LibraryShare, Note, AuditEvent, and User.org_role.
ML (Embedding) entities arrive in later phases.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from pgvector.sqlalchemy import Vector

# Embedding dimensionality of the configured Ollama model (nomic-embed-text = 768).
# This is the on-disk width of item.embedding; changing models means a migration.
EMBEDDING_DIM = 768


class Base(DeclarativeBase):
    pass


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


# Association tables
item_collection = Table(
    "item_collection",
    Base.metadata,
    Column("item_id", ForeignKey("item.id", ondelete="CASCADE"), primary_key=True),
    Column("collection_id", ForeignKey("collection.id", ondelete="CASCADE"), primary_key=True),
)

item_tag = Table(
    "item_tag",
    Base.metadata,
    Column("item_id", ForeignKey("item.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tag.id", ondelete="CASCADE"), primary_key=True),
)

user_group = Table(
    "user_group",
    Base.metadata,
    Column("user_id", ForeignKey("user_account.id", ondelete="CASCADE"), primary_key=True),
    Column("group_id", ForeignKey("group_team.id", ondelete="CASCADE"), primary_key=True),
)

# Org-level roles and per-resource access levels (kept as plain strings + constants)
ORG_ROLES = ("admin", "member", "read-only")
ACCESS_LEVELS = ("view", "edit", "manage")
_ACCESS_RANK = {"view": 1, "edit": 2, "manage": 3}


class User(Base):
    __tablename__ = "user_account"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    sub: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    org_role: Mapped[str] = mapped_column(String(32), default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    groups: Mapped[list["Group"]] = relationship(secondary=user_group, lazy="selectin")


class Group(Base):
    """A team. Membership is typically sourced from Authentik group claims."""

    __tablename__ = "group_team"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    # Identifier of the matching Authentik group (name/pk), if synced from OIDC claims
    authentik_ref: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Library(Base):
    """A shared library / project, owned by exactly one user OR one group."""

    __tablename__ = "library"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), nullable=True
    )
    owner_group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("group_team.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "(owner_id IS NOT NULL) <> (owner_group_id IS NOT NULL)",
            name="ck_library_single_owner",
        ),
    )


class Collection(Base):
    """Nestable folder within a Library (Zotero-style)."""

    __tablename__ = "collection"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    library_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("library.id", ondelete="CASCADE"))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("collection.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BibFile(Base):
    """A source .bib file imported into a Library. A project may have 1..N."""

    __tablename__ = "bib_file"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    library_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("library.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(512))
    entry_count: Mapped[int] = mapped_column(Integer, default=0)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Item(Base):
    """A bibliographic reference. Canonical metadata is CSL-JSON; raw BibTeX kept for
    lossless export with preserved citation keys."""

    __tablename__ = "item"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    library_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("library.id", ondelete="CASCADE"), index=True
    )
    source_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("bib_file.id", ondelete="SET NULL"), nullable=True
    )
    citation_key: Mapped[str] = mapped_column(String(255), default="")
    type: Mapped[str] = mapped_column(String(64), default="document")  # CSL type
    csl_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    raw_bibtex: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Denormalized for search / list views
    title: Mapped[str] = mapped_column(Text, default="")
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    # Full-text search (Phase 3a/3b). `search_text` is the metadata body (abstract,
    # authors, container, citation key), maintained in items._denormalize; `pdf_text` is
    # the concatenated text of the item's PDF attachments, maintained in the attachments
    # router. `search_tsv` is a generated tsvector weighting title (A) > metadata (B) >
    # PDF body (C). PDF body is capped to keep the tsvector under Postgres' ~1 MiB limit.
    search_text: Mapped[str] = mapped_column(Text, default="")
    pdf_text: Mapped[str] = mapped_column(Text, default="")
    search_tsv = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english', coalesce(title, '')), 'A') || "
            "setweight(to_tsvector('english', coalesce(search_text, '')), 'B') || "
            "setweight(to_tsvector('english', left(coalesce(pdf_text, ''), 500000)), 'C')",
            persisted=True,
        ),
        nullable=True,
    )

    # Semantic search (Phase 3c): one embedding per item over title+abstract. Nullable —
    # items created while Ollama is down stay null until the reindex endpoint backfills.
    embedding = mapped_column(Vector(EMBEDDING_DIM), nullable=True)

    version: Mapped[int] = mapped_column(Integer, default=1)  # optimistic locking
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    attachments: Mapped[list["Attachment"]] = relationship(
        back_populates="item", cascade="all, delete-orphan", lazy="selectin"
    )
    tags: Mapped[list["Tag"]] = relationship(secondary=item_tag, lazy="selectin")

    __table_args__ = (
        # Citation keys are preserved verbatim; collisions are flagged, not blocked,
        # so this index is non-unique.
        Index("ix_item_library_citation_key", "library_id", "citation_key"),
        Index("ix_item_search_tsv", "search_tsv", postgresql_using="gin"),
        # Approximate-NN index for cosine distance (pgvector HNSW).
        Index(
            "ix_item_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class Attachment(Base):
    __tablename__ = "attachment"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("item.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(128), default="application/pdf")
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(String(512))
    # Extracted plain text (PDFs), cached for full-text search (Phase 3b).
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    item: Mapped["Item"] = relationship(back_populates="attachments")


class Tag(Base):
    __tablename__ = "tag"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    library_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("library.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(32), default="manual")  # manual | ml


class LibraryShare(Base):
    """Grants a Group an access level on a Library (view | edit | manage)."""

    __tablename__ = "library_share"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    library_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("library.id", ondelete="CASCADE"))
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("group_team.id", ondelete="CASCADE"))
    access_level: Mapped[str] = mapped_column(String(16), default="view")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("library_id", "group_id", name="uq_library_share_library_group"),
    )


class Note(Base):
    """A free-text (Markdown) note on an Item, authored by a user."""

    __tablename__ = "note"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("item.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL"), nullable=True
    )
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AuditEvent(Base):
    """Append-only history. One row per mutation; `before`/`after` are JSON snapshots."""

    __tablename__ = "audit_event"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL"), nullable=True
    )
    library_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("library.id", ondelete="CASCADE"), nullable=True, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(32))  # item | note | library | share
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    operation: Mapped[str] = mapped_column(String(16))  # create | update | delete | restore
    summary: Mapped[str] = mapped_column(Text, default="")
    before: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
