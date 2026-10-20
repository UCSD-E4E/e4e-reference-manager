"""one-time PDF upload links

Short-lived URLs that accept a single PDF upload for an item (agents `curl` a local
file to them). Only the SHA-256 of each link's secret is stored.

Revision ID: c1e3a5b7d9f2
Revises: b9d4f6a8c0e2
Create Date: 2026-10-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c1e3a5b7d9f2'
down_revision: Union[str, None] = 'b9d4f6a8c0e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pdf_upload_link",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["item_id"], ["item.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["user_account.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_pdf_upload_link_item_id", "pdf_upload_link", ["item_id"])


def downgrade() -> None:
    op.drop_index("ix_pdf_upload_link_item_id", table_name="pdf_upload_link")
    op.drop_table("pdf_upload_link")
