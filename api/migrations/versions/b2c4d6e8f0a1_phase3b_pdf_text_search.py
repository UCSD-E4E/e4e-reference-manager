"""phase 3b: cache extracted PDF text and fold it into full-text search

Adds attachment.text (cached extracted plain text) and item.pdf_text (the item's
concatenated attachment text), and redefines item.search_tsv to weight title (A) >
metadata (B) > PDF body (C). The PDF body is capped to keep the tsvector under
Postgres' ~1 MiB limit.

Revision ID: b2c4d6e8f0a1
Revises: a1f3c2d4e5b6
Create Date: 2026-05-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b2c4d6e8f0a1'
down_revision: Union[str, None] = 'a1f3c2d4e5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TSV_2 = (
    "setweight(to_tsvector('english', coalesce(title, '')), 'A') || "
    "setweight(to_tsvector('english', coalesce(search_text, '')), 'B')"
)
_TSV_3 = _TSV_2 + (
    " || setweight(to_tsvector('english', left(coalesce(pdf_text, ''), 500000)), 'C')"
)


def _set_tsv_expr(expr: str) -> None:
    """Redefine the generated search_tsv column + its GIN index to use `expr`."""
    op.drop_index("ix_item_search_tsv", table_name="item")
    op.drop_column("item", "search_tsv")
    op.add_column(
        "item",
        sa.Column(
            "search_tsv",
            postgresql.TSVECTOR(),
            sa.Computed(expr, persisted=True),
            nullable=True,
        ),
    )
    op.create_index("ix_item_search_tsv", "item", ["search_tsv"], postgresql_using="gin")


def upgrade() -> None:
    op.add_column("attachment", sa.Column("text", sa.Text(), nullable=True))
    op.add_column(
        "item", sa.Column("pdf_text", sa.Text(), nullable=False, server_default="")
    )
    _set_tsv_expr(_TSV_3)


def downgrade() -> None:
    _set_tsv_expr(_TSV_2)
    op.drop_column("item", "pdf_text")
    op.drop_column("attachment", "text")
