"""add gateway_targets table and tools.target_id

Revision ID: b7c8d9e0f1a2
Revises: e8f9a0b1c2d3
Create Date: 2026-09-08

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b7c8d9e0f1a2"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create gateway_targets table and add tools.target_id column."""
    op.create_table(
        "gateway_targets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("rest", "mcp", name="gateway_target_kind", create_constraint=True),
            nullable=False,
        ),
        sa.Column("base_url", sa.String(1024), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("credentials", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "name", name="uq_gateway_targets_workspace_name"),
    )
    op.create_index(
        "idx_gateway_targets_workspace_name",
        "gateway_targets",
        ["workspace_id", "name"],
        unique=True,
    )
    op.add_column("tools", sa.Column("target_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    """Drop tools.target_id column and gateway_targets table."""
    op.drop_column("tools", "target_id")
    op.drop_index("idx_gateway_targets_workspace_name", table_name="gateway_targets")
    op.drop_table("gateway_targets")
    sa.Enum(name="gateway_target_kind").drop(op.get_bind(), checkfirst=True)
