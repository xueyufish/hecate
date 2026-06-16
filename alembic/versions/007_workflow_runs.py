"""Add workflow_runs table for test run history.

Revision ID: 007_workflow_runs
Revises: 006_hybrid_search
Create Date: 2026-05-31
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "007_workflow_runs"
down_revision = "006_hybrid_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create workflow_runs table for persisting test run history."""

    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("workflow_id", sa.UUID(), sa.ForeignKey("workflows.id"), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("mock", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("input_data", sa.JSON(), nullable=False),
        sa.Column("node_results", sa.JSON(), nullable=False),
        sa.Column("total_duration_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_workflow_runs_workflow", "workflow_runs", ["workflow_id"])


def downgrade() -> None:
    """Drop workflow_runs table."""

    op.drop_index("idx_workflow_runs_workflow", table_name="workflow_runs")
    op.drop_table("workflow_runs")
