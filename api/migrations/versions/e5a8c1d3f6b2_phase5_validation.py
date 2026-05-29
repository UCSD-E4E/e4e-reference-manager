"""phase 5: per-item validation verdict column (anti-hallucination)

Adds item.validation JSONB to cache the latest verdict from validate_item_csl
(Crossref / arXiv lookups). Nullable: items are unvalidated until the user runs the
validate endpoint.

Revision ID: e5a8c1d3f6b2
Revises: d4f6b8a0c2e1
Create Date: 2026-05-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e5a8c1d3f6b2'
down_revision: Union[str, None] = 'd4f6b8a0c2e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "item",
        sa.Column("validation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("item", "validation")
