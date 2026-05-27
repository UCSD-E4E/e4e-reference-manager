"""phase 4: PDF annotations table

Adds the `annotation` table (highlights/comments anchored to a PDF attachment):
page + normalized rects (JSONB) + color + quote + optional comment, authored by a user.

Revision ID: d4f6b8a0c2e1
Revises: c3e5a7b9d1f2
Create Date: 2026-05-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd4f6b8a0c2e1'
down_revision: Union[str, None] = 'c3e5a7b9d1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "annotation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("attachment_id", sa.Uuid(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("page", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "rects",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("color", sa.String(length=16), server_default="#ffd54f", nullable=False),
        sa.Column("quote", sa.Text(), server_default="", nullable=False),
        sa.Column("comment", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["attachment_id"], ["attachment.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["user_account.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_annotation_attachment_id", "annotation", ["attachment_id"])


def downgrade() -> None:
    op.drop_index("ix_annotation_attachment_id", table_name="annotation")
    op.drop_table("annotation")
