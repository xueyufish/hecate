"""Add memory_blocks and memories tables.

Revision ID: 003_memory_system
Revises: 002_workflow_management
Create Date: 2026-05-28
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "003_memory_system"
down_revision = "002_workflow_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create memory_blocks and memories tables."""

    op.create_table(
        "memory_blocks",
        sa.Column("id", sa.Uuid(), primary_key=True, default=uuid.uuid4),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("limit", sa.Integer(), nullable=False, server_default="2000"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_memory_blocks_agent", "memory_blocks", ["agent_id"])
    op.create_index("idx_memory_blocks_agent_label", "memory_blocks", ["agent_id", "label"], unique=True)

    op.create_table(
        "memories",
        sa.Column("id", sa.Uuid(), primary_key=True, default=uuid.uuid4),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("memory_type", sa.String(50), nullable=False, server_default="semantic"),
        sa.Column("importance", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("access_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    # scope is JSON type — skip index (btree/gin require JSONB or operator class)
    op.create_index("idx_memories_type", "memories", ["memory_type"])
    op.create_index("idx_memories_importance", "memories", ["importance"])


def downgrade() -> None:
    """Drop memories and memory_blocks tables."""

    op.drop_index("idx_memories_importance", table_name="memories")
    op.drop_index("idx_memories_type", table_name="memories")
    op.drop_index("idx_memories_scope", table_name="memories")
    op.drop_table("memories")

    op.drop_index("idx_memory_blocks_agent_label", table_name="memory_blocks")
    op.drop_index("idx_memory_blocks_agent", table_name="memory_blocks")
    op.drop_table("memory_blocks")
