"""channels: first-class publishing channels bound to agent versions (1.3.20)

Revision ID: i5d6e7f8a9b0
Revises: h4c5d6e7f8a9
Create Date: 2026-09-14

Adds (Agent Versioning & Channel Publishing 1.3.20, PR-2):
- ``channels`` — the alias-indirection layer between external surfaces
  and an agent's published version. A channel binds one agent with a
  ``bind_mode`` of ``published`` (tracks the latest published version) or
  ``pinned`` (locked to ``pinned_version`` until explicitly repointed).
  ``type`` v1 wires ``api`` and ``im``; ``embed`` / ``webhook`` are
  accepted as reserved placeholders (status ``unwired``).

All changes are additive; existing rows are unaffected.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "i5d6e7f8a9b0"
down_revision: str | None = "h4c5d6e7f8a9"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "channels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("bind_mode", sa.String(20), nullable=False, server_default="published"),
        sa.Column("pinned_version", sa.Integer(), nullable=True),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
    )
    op.create_index("idx_channels_workspace", "channels", ["workspace_id", "deleted"])
    op.create_index("idx_channels_agent", "channels", ["agent_id", "deleted"])


def downgrade() -> None:
    op.drop_index("idx_channels_agent", table_name="channels")
    op.drop_index("idx_channels_workspace", table_name="channels")
    op.drop_table("channels")
