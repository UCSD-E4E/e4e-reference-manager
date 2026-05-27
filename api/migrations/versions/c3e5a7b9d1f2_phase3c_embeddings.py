"""phase 3c: per-item embedding column + HNSW index for semantic search

Adds item.embedding vector(768) (nomic-embed-text width) and an HNSW cosine index.
The `vector` extension is already created by the initial migration. Existing rows stay
null until the /libraries/{id}/reindex endpoint backfills them.

Revision ID: c3e5a7b9d1f2
Revises: b2c4d6e8f0a1
Create Date: 2026-05-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = 'c3e5a7b9d1f2'
down_revision: Union[str, None] = 'b2c4d6e8f0a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column("item", sa.Column("embedding", Vector(768), nullable=True))
    op.create_index(
        "ix_item_embedding_hnsw",
        "item",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_item_embedding_hnsw", table_name="item")
    op.drop_column("item", "embedding")
