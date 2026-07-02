"""Add workflows and workflow_versions tables.

Revision ID: 002_workflow_management
Revises: 001_context_engineering
Create Date: 2026-05-28
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "002_workflow_management"
down_revision = "001_context_engineering"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create workflows and workflow_versions tables."""

    op.create_table(
        "workflows",
        sa.Column("id", sa.Uuid(), primary_key=True, default=uuid.uuid4),
        sa.Column("workspace_id", sa.Uuid(), nullable=False, server_default="00000000-0000-0000-0000-000000000000"),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_workflows_workspace", "workflows", ["workspace_id"])

    op.create_table(
        "workflow_versions",
        sa.Column("id", sa.Uuid(), primary_key=True, default=uuid.uuid4),
        sa.Column("workflow_id", sa.Uuid(), sa.ForeignKey("workflows.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("graph_dsl", sa.JSON(), nullable=False),
        sa.Column("compiled_graph", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("change_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_workflow_versions_workflow", "workflow_versions", ["workflow_id"])
    op.create_index("idx_workflow_versions_unique", "workflow_versions", ["workflow_id", "version"], unique=True)


def downgrade() -> None:
    """Drop workflow_versions and workflows tables."""

    op.drop_index("idx_workflow_versions_unique", table_name="workflow_versions")
    op.drop_index("idx_workflow_versions_workflow", table_name="workflow_versions")
    op.drop_table("workflow_versions")

    op.drop_index("idx_workflows_workspace", table_name="workflows")
    op.drop_table("workflows")
