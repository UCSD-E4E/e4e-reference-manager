"""citation graph

Stored citation links between a library's papers (from OpenAlex reference lists and
GROBID on attached PDFs), each paper's OpenAlex work id and when its citations were
last computed, and each PDF's extracted reference list (cached).

Revision ID: d2f4b6c8e0a3
Revises: c1e3a5b7d9f2
Create Date: 2026-10-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd2f4b6c8e0a3'
down_revision: Union[str, None] = 'c1e3a5b7d9f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("item", sa.Column("openalex_id", sa.String(length=32), nullable=True))
    op.add_column(
        "item", sa.Column("citations_checked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_item_openalex_id", "item", ["openalex_id"])
    op.add_column(
        "attachment",
        sa.Column("references", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_table(
        "citation",
        sa.Column("citing_id", sa.Uuid(), nullable=False),
        sa.Column("cited_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(["citing_id"], ["item.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cited_id"], ["item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("citing_id", "cited_id", "source"),
    )
    op.create_index("ix_citation_cited_id", "citation", ["cited_id"])


def downgrade() -> None:
    op.drop_index("ix_citation_cited_id", table_name="citation")
    op.drop_table("citation")
    op.drop_column("attachment", "references")
    op.drop_index("ix_item_openalex_id", table_name="item")
    op.drop_column("item", "citations_checked_at")
    op.drop_column("item", "openalex_id")
