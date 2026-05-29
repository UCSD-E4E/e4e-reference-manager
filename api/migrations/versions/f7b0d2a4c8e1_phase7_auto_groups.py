"""phase 7: JabRef-style auto-groups table

Live, rule-driven groups within a Library. `kind` selects the rule type and `params`
holds kind-specific JSONB config. Membership is recomputed on demand at query time.

Revision ID: f7b0d2a4c8e1
Revises: e5a8c1d3f6b2
Create Date: 2026-05-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f7b0d2a4c8e1'
down_revision: Union[str, None] = 'e5a8c1d3f6b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "auto_group",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("library_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["library_id"], ["library.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_auto_group_library_id", "auto_group", ["library_id"])


def downgrade() -> None:
    op.drop_index("ix_auto_group_library_id", table_name="auto_group")
    op.drop_table("auto_group")
