"""Add search_mode and sparse_weight to knowledge_bases.

Revision ID: 006_hybrid_search
Revises: 005_users
Create Date: 2026-05-31
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "006_hybrid_search"
down_revision = "005_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add search_mode and sparse_weight columns to knowledge_bases."""

    op.add_column(
        "knowledge_bases",
        sa.Column("search_mode", sa.String(20), nullable=False, server_default="hybrid"),
    )
    op.add_column(
        "knowledge_bases",
        sa.Column("sparse_weight", sa.Float(), nullable=False, server_default="0.3"),
    )


def downgrade() -> None:
    """Remove search_mode and sparse_weight columns from knowledge_bases."""

    op.drop_column("knowledge_bases", "sparse_weight")
    op.drop_column("knowledge_bases", "search_mode")
