"""Add guardrail_config JSONB column to agents table.

Revision ID: 014_agent_guardrail_config
Revises: 013_tenant_isolation_workspace_id
Create Date: 2026-06-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "014_agent_guardrail_config"
down_revision = "013_tenant_isolation_workspace_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("guardrail_config", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "guardrail_config")
