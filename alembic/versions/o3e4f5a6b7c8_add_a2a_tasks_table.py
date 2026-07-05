"""add a2a_tasks table

Revision ID: o3e4f5a6b7c8
Revises: n2d3e4f5a6b7
Create Date: 2026-07-04 13:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "o3e4f5a6b7c8"
down_revision: str = "n2d3e4f5a6b7"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create a2a_tasks table for A2A task lifecycle persistence."""
    op.create_table(
        "a2a_tasks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("context_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="submitted"),
        sa.Column("status_message", sa.JSON(), nullable=True),
        sa.Column("artifacts", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("history", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_a2a_tasks_task_id", "a2a_tasks", ["task_id"])


def downgrade() -> None:
    """Drop a2a_tasks table."""
    op.drop_index("idx_a2a_tasks_task_id", table_name="a2a_tasks")
    op.drop_table("a2a_tasks")
