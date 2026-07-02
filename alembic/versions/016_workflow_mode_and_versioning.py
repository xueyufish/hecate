"""Add execution_mode, published_version to workflows and labels to workflow_versions.

Revision ID: 016_workflow_mode_and_versioning
Revises: 015_pii_mappings_table
Create Date: 2026-06-11
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "016_workflow_mode_and_versioning"
down_revision = "015_pii_mappings_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column("execution_mode", sa.String(20), nullable=False, server_default="conversational"),
    )
    op.add_column("workflows", sa.Column("published_version", sa.Integer(), nullable=True))
    op.add_column("workflow_versions", sa.Column("labels", sa.JSON(), nullable=True, server_default="[]"))


def downgrade() -> None:
    op.drop_column("workflow_versions", "labels")
    op.drop_column("workflows", "published_version")
    op.drop_column("workflows", "execution_mode")
