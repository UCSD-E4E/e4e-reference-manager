"""library trash for references

Deleting a reference now sets `deleted_at` (and who did it) instead of removing the
row; trashed items are hidden from every query (app.trash) and purged after 30 days.

Revision ID: b9d4f6a8c0e2
Revises: a8c3e5f7b9d0
Create Date: 2026-10-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b9d4f6a8c0e2'
down_revision: Union[str, None] = 'a8c3e5f7b9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("item", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("item", sa.Column("deleted_by_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "item_deleted_by_id_fkey",
        "item",
        "user_account",
        ["deleted_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_item_deleted_at", "item", ["deleted_at"])


def downgrade() -> None:
    # Trashed items would reappear as live ones: drop them first, as a purge would.
    op.execute("DELETE FROM item WHERE deleted_at IS NOT NULL")
    op.drop_index("ix_item_deleted_at", table_name="item")
    op.drop_constraint("item_deleted_by_id_fkey", "item", type_="foreignkey")
    op.drop_column("item", "deleted_by_id")
    op.drop_column("item", "deleted_at")
