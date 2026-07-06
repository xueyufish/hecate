"""add model_cost_budgets table

Revision ID: t8c9d0e1f2a3
Revises: q5f6a7b8c9d0
Create Date: 2026-07-06 13:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "t8c9d0e1f2a3"
down_revision: str = "q5f6a7b8c9d0"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "model_cost_budgets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("limit_amount", sa.Float(), nullable=False),
        sa.Column("period", sa.String(20), nullable=False, server_default="monthly"),
        sa.Column("currency", sa.String(10), nullable=False, server_default="USD"),
        sa.Column("policy", sa.String(10), nullable=False, server_default="alert"),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_cost_budgets_workspace", "model_cost_budgets", ["workspace_id", "deleted"])
    op.create_index(
        "idx_cost_budgets_scope_target", "model_cost_budgets", ["scope", "target_id", "workspace_id", "deleted"]
    )


def downgrade() -> None:
    op.drop_index("idx_cost_budgets_scope_target", table_name="model_cost_budgets")
    op.drop_index("idx_cost_budgets_workspace", table_name="model_cost_budgets")
    op.drop_table("model_cost_budgets")
