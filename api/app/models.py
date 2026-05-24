"""SQLAlchemy ORM models for Phase 0.

Entities: User, Library (= Project), Collection, BibFile, Item (= Reference),
Attachment, Tag. Collaboration/history (Groups, Notes, AuditEvent) and ML
(Embedding) entities arrive in later phases.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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


class User(Base):
    __tablename__ = "user_account"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    sub: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Library(Base):
    """A shared library / project. Phase 0: owned by a user. Group ownership in Phase 1."""

    __tablename__ = "library"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
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

    version: Mapped[int] = mapped_column(Integer, default=1)  # optimistic locking
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    attachments: Mapped[list["Attachment"]] = relationship(
        back_populates="item", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        # Citation keys are preserved verbatim; collisions are flagged, not blocked,
        # so this index is non-unique.
        Index("ix_item_library_citation_key", "library_id", "citation_key"),
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    item: Mapped["Item"] = relationship(back_populates="attachments")


class Tag(Base):
    __tablename__ = "tag"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    library_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("library.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(32), default="manual")  # manual | ml
