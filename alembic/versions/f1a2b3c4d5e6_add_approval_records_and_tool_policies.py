"""add_approval_records_and_tool_policies

Revision ID: f1a2b3c4d5e6
Revises: e0e69d57120a
Create Date: 2026-06-19 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "e0e69d57120a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("tool_name", sa.String(255), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=True),
        sa.Column("user_id", sa.String(36), nullable=True),
        sa.Column("scope", sa.String(20), nullable=False, server_default="once"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("risk_level", sa.String(20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "idx_approval_workspace_tool",
        "approval_records",
        ["workspace_id", "tool_name", "deleted", "deleted_at"],
    )

    op.create_table(
        "tool_policies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("rule_action", sa.String(20), nullable=False),
        sa.Column("tool_pattern", sa.String(255), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "idx_tool_policy_workspace_pattern_action",
        "tool_policies",
        ["workspace_id", "tool_pattern", "rule_action", "deleted", "deleted_at"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("idx_tool_policy_workspace_pattern_action", table_name="tool_policies")
    op.drop_table("tool_policies")
    op.drop_index("idx_approval_workspace_tool", table_name="approval_records")
    op.drop_table("approval_records")
