"""add hook_configs, tool_policy_rules, agent_policy_configs tables

Revision ID: 7c3d4e5f6a7b
Revises: 6b2c3d4e5f6a
Create Date: 2026-08-21

Resolves a long-standing schema gap: three BaseModel-derived tables
referenced by management API routes (hooks, tool policy rules, per-agent
policy configs) were never created by any alembic migration. The ORM
models exist and FastAPI routes call them, but every request to
``GET/POST/DELETE /api/management/hooks`` or
``GET/POST/DELETE /api/management/tool-policies/rules`` (and the
per-agent policy-config endpoints) raised ``ProgrammingError: relation
... does not exist`` at the SQLAlchemy layer.

This migration creates all three tables with the BaseModel default
columns (``id``, ``created_at``, ``updated_at``, ``deleted``,
``deleted_at``) so the ORM and table stay in sync — applying the
lesson learned from 5a1b2c3d4e5f / 6b2c3d4e5f6a / 010_add_deleted:
always include both soft-delete columns at table-creation time, never
rely on a follow-up ``add_column`` migration to paper over the gap.

Indexes mirror the ``__table_args__`` declared on each model.

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "7c3d4e5f6a7b"
down_revision: str | None = "6b2c3d4e5f6a"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create hook_configs, tool_policy_rules, agent_policy_configs."""
    # ---- hook_configs ----
    op.create_table(
        "hook_configs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("event", sa.String(length=50), nullable=False),
        sa.Column("matcher", sa.String(length=500), nullable=True),
        sa.Column("command", sa.String(length=1000), nullable=False),
        sa.Column("timeout", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_hook_configs_ws_agent", "hook_configs", ["workspace_id", "agent_id"])

    # ---- tool_policy_rules ----
    op.create_table(
        "tool_policy_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("tool_pattern", sa.String(length=500), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("arg_conditions", sa.JSON(), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_tool_policy_rules_ws_agent", "tool_policy_rules", ["workspace_id", "agent_id"])

    # ---- agent_policy_configs ----
    op.create_table(
        "agent_policy_configs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False, server_default="default"),
        sa.Column("tool_allowlist", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("tool_denylist", sa.JSON(), nullable=False, server_default="[]"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # AgentPolicyConfigModel.agent_id is mapped unique=True → enforce at DB level.
    op.create_index(
        "uq_agent_policy_configs_agent_id",
        "agent_policy_configs",
        ["agent_id"],
        unique=True,
    )


def downgrade() -> None:
    """Drop hook_configs, tool_policy_rules, agent_policy_configs."""
    op.drop_index("uq_agent_policy_configs_agent_id", table_name="agent_policy_configs")
    op.drop_table("agent_policy_configs")
    op.drop_index("idx_tool_policy_rules_ws_agent", table_name="tool_policy_rules")
    op.drop_table("tool_policy_rules")
    op.drop_index("idx_hook_configs_ws_agent", table_name="hook_configs")
    op.drop_table("hook_configs")
