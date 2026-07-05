"""add agent_card_keys table

Revision ID: p4f5a6b7c8d9
Revises: o3e4f5a6b7c8
Create Date: 2026-07-04 14:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "p4f5a6b7c8d9"
down_revision: str = "o3e4f5a6b7c8"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create agent_card_keys table for A2A signing keys."""
    op.create_table(
        "agent_card_keys",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("kid", sa.String(36), nullable=False),
        sa.Column("private_key", sa.JSON(), nullable=False),
        sa.Column("public_key", sa.JSON(), nullable=False),
        sa.Column("algorithm", sa.String(10), nullable=False, server_default="ES256"),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kid"),
    )
    op.create_index("idx_agent_card_keys_workspace", "agent_card_keys", ["workspace_id", "deleted"])


def downgrade() -> None:
    """Drop agent_card_keys table."""
    op.drop_index("idx_agent_card_keys_workspace", table_name="agent_card_keys")
    op.drop_table("agent_card_keys")
