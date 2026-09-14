"""agent versioning: agent_versions table + agents publish/gate columns (1.3.20)

Revision ID: h4c5d6e7f8a9
Revises: g3b4c5d6e7f8
Create Date: 2026-09-14

Adds (Agent Versioning & Channel Publishing 1.3.20, PR-1):
- ``agent_versions`` — immutable snapshots of an agent's own config with
  pinned workflow references and a reference manifest (content hashes for
  not-yet-versioned resources). ``(agent_id, version)`` is unique; version
  numbers are monotonically increasing per agent.
- ``agents.published_version`` — nullable pointer to the published version
  number (mirrors ``workflows.published_version``).
- ``agents.evaluation_gate`` — nullable JSON publish-gate configuration
  (same shape as ``workflows.evaluation_gate``).

All changes are additive; existing rows are unaffected.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "h4c5d6e7f8a9"
down_revision: str | None = "g3b4c5d6e7f8"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "agent_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False, server_default=""),
        sa.Column("change_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("config_snapshot", sa.JSON(), nullable=False),
        sa.Column("pinned_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("ref_manifest", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),
    )
    op.create_index("idx_agent_versions_agent", "agent_versions", ["agent_id", "deleted"])
    op.create_index("idx_agent_versions_workspace", "agent_versions", ["workspace_id", "deleted"])

    op.add_column("agents", sa.Column("published_version", sa.Integer(), nullable=True))
    op.add_column("agents", sa.Column("evaluation_gate", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "evaluation_gate")
    op.drop_column("agents", "published_version")
    op.drop_index("idx_agent_versions_workspace", table_name="agent_versions")
    op.drop_index("idx_agent_versions_agent", table_name="agent_versions")
    op.drop_table("agent_versions")
