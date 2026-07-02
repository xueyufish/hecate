"""Add quota management tables.

Creates quotas and quota_usage tables for per-tenant resource limits.

Revision ID: g8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-06-21 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "g8b9c0d1e2f3"
down_revision: str | None = "f7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_WORKSPACE = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    op.create_table(
        "quotas",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("resource_type", sa.String(32), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("scope_id", sa.String(36), nullable=False),
        sa.Column("limit_value", sa.Float, nullable=False),
        sa.Column("soft_limit", sa.Float, nullable=True),
        sa.Column("window_type", sa.String(16), nullable=False),
        sa.Column("enforcement", sa.String(16), nullable=False, server_default="hard_reject"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("workspace_id", sa.String(36), nullable=False, server_default=_DEFAULT_WORKSPACE),
    )
    op.create_index("ix_quotas_workspace_scope_resource", "quotas", ["workspace_id", "scope", "resource_type"])
    op.create_index("ix_quotas_scope_id", "quotas", ["scope_id"])

    op.create_table(
        "quota_usage",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quota_id", sa.String(36), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_value", sa.Float, nullable=False, server_default="0"),
        sa.Column("last_updated", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("soft_limit_triggered", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("workspace_id", sa.String(36), nullable=False, server_default=_DEFAULT_WORKSPACE),
    )
    op.create_index("ix_quota_usage_quota_period", "quota_usage", ["quota_id", "period_start"])
    op.create_index("ix_quota_usage_quota_id", "quota_usage", ["quota_id"])


def downgrade() -> None:
    op.drop_index("ix_quota_usage_quota_id", table_name="quota_usage")
    op.drop_index("ix_quota_usage_quota_period", table_name="quota_usage")
    op.drop_table("quota_usage")
    op.drop_index("ix_quotas_scope_id", table_name="quotas")
    op.drop_index("ix_quotas_workspace_scope_resource", table_name="quotas")
    op.drop_table("quotas")
