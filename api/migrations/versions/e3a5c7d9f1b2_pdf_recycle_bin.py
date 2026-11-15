"""pdf recycle bin

An attachment can be moved to its item's recycle bin: who moved it, when and why.

Revision ID: e3a5c7d9f1b2
Revises: d2f4b6c8e0a3
Create Date: 2026-11-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e3a5c7d9f1b2'
down_revision: Union[str, None] = 'd2f4b6c8e0a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "attachment", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("attachment", sa.Column("archived_by_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "attachment_archived_by_id_fkey",
        "attachment",
        "user_account",
        ["archived_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("attachment", sa.Column("archive_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    # Binned PDFs would reappear as live ones: drop them first, as deleting would.
    op.execute("DELETE FROM attachment WHERE archived_at IS NOT NULL")
    op.drop_column("attachment", "archive_reason")
    op.drop_constraint(
        "attachment_archived_by_id_fkey", "attachment", type_="foreignkey"
    )
    op.drop_column("attachment", "archived_by_id")
    op.drop_column("attachment", "archived_at")
