"""add evaluation_backflow_rules for automated trace backflow

Revision ID: d9e0f1a2b3c5
Revises: c8d9e0f1a2b4
Create Date: 2026-09-12

Adds (Automated Trace Backflow 7.2d):
- ``evaluation_backflow_rules`` table — persisted rules selecting scored
  production traces of one online evaluation task by score-band filters and
  materializing them into one evaluation dataset on explicit trigger
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "d9e0f1a2b3c5"
down_revision: str = "c8d9e0f1a2b4"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_backflow_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("limit", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("max_turns", sa.Integer(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evaluation_backflow_rules_task_id", "evaluation_backflow_rules", ["task_id"])
    op.create_index("ix_evaluation_backflow_rules_dataset_id", "evaluation_backflow_rules", ["dataset_id"])
    op.create_index(
        "idx_eval_backflow_rules_workspace",
        "evaluation_backflow_rules",
        ["workspace_id", "deleted"],
    )


def downgrade() -> None:
    op.drop_index("idx_eval_backflow_rules_workspace", table_name="evaluation_backflow_rules")
    op.drop_index("ix_evaluation_backflow_rules_dataset_id", table_name="evaluation_backflow_rules")
    op.drop_index("ix_evaluation_backflow_rules_task_id", table_name="evaluation_backflow_rules")
    op.drop_table("evaluation_backflow_rules")
