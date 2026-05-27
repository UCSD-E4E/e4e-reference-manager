"""phase 3a: full-text search on item (search_text + generated tsvector + GIN index)

Adds a denormalized `search_text` body column and a STORED generated `search_tsv`
tsvector that weights the title (A) above the body (B). The app maintains `search_text`
in items._denormalize; the tsvector and GIN index are managed by Postgres.

Existing rows keep title search (the generated expression reads the `title` column
directly); their abstract/author body becomes searchable once the item is next saved or
the Phase 3c reindex runs. No data backfill here.

Revision ID: a1f3c2d4e5b6
Revises: fbb0a2751cc8
Create Date: 2026-05-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a1f3c2d4e5b6'
down_revision: Union[str, None] = 'fbb0a2751cc8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TSV_EXPR = (
    "setweight(to_tsvector('english', coalesce(title, '')), 'A') || "
    "setweight(to_tsvector('english', coalesce(search_text, '')), 'B')"
)


def upgrade() -> None:
    op.add_column(
        "item",
        sa.Column("search_text", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "item",
        sa.Column(
            "search_tsv",
            postgresql.TSVECTOR(),
            sa.Computed(_TSV_EXPR, persisted=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_item_search_tsv", "item", ["search_tsv"], postgresql_using="gin"
    )


def downgrade() -> None:
    op.drop_index("ix_item_search_tsv", table_name="item")
    op.drop_column("item", "search_tsv")
    op.drop_column("item", "search_text")
