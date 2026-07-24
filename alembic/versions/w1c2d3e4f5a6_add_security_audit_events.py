"""add security_audit_events table

Revision ID: w1c2d3e4f5a6
Revises: v0b1c2d3e4f5
Create Date: 2026-07-23 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "w1c2d3e4f5a6"
down_revision: str = "v0b1c2d3e4f5"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "security_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=True),
        sa.Column("tool_name", sa.String(length=255), nullable=False),
        sa.Column("arguments_hash", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=50), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("policy_version", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("on_behalf_of_user", sa.String(length=255), nullable=True),
        sa.Column("layer_results", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_security_audit_agent_ts", "security_audit_events", ["agent_id", "timestamp"])
    op.create_index("ix_security_audit_workspace_ts", "security_audit_events", ["workspace_id", "timestamp"])
    op.create_index("ix_security_audit_decision_ts", "security_audit_events", ["decision", "timestamp"])


def downgrade() -> None:
    op.drop_index("ix_security_audit_decision_ts", table_name="security_audit_events")
    op.drop_index("ix_security_audit_workspace_ts", table_name="security_audit_events")
    op.drop_index("ix_security_audit_agent_ts", table_name="security_audit_events")
    op.drop_table("security_audit_events")
