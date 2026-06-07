"""Rename qdrant_collection to collection_name on knowledge_bases.

Revision ID: 011_rename_qdrant_collection
Revises: 010_add_deleted
Create Date: 2026-06-07
"""

from __future__ import annotations

from alembic import op

# revision identifiers
revision = "011_rename_qdrant_collection"
down_revision = "010_add_deleted"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "knowledge_bases",
        "qdrant_collection",
        new_column_name="collection_name",
    )


def downgrade() -> None:
    op.alter_column(
        "knowledge_bases",
        "collection_name",
        new_column_name="qdrant_collection",
    )
