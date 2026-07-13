"""add plugin model table

Revision ID: v0b1c2d3e4f5
Revises: u9d0e1f2a3b4
Create Date: 2026-07-12 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "v0b1c2d3e4f5"
down_revision: str = "u9d0e1f2a3b4"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "plugins",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False, server_default="0.0.0"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="installed"),
        sa.Column("entry", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("manifest", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_plugins_name", "plugins", ["name"])
    op.create_index("ix_plugins_workspace_id", "plugins", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_plugins_workspace_id", table_name="plugins")
    op.drop_index("ix_plugins_name", table_name="plugins")
    op.drop_table("plugins")
