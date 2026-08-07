"""add session_states table

Revision ID: h9c0d1e2f3a4
Revises: z4f5a6b7c8d9
Create Date: 2026-08-01

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "h9c0d1e2f3a4"
down_revision = "z4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create session_states table for distributed session state persistence (13.4a)."""
    op.create_table(
        "session_states",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("superstep", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("org_id", "user_id", "session_id"),
    )
    op.create_index(
        "idx_session_states_org_user_updated",
        "session_states",
        ["org_id", "user_id", "updated_at"],
    )


def downgrade() -> None:
    """Drop session_states table."""
    op.drop_index("idx_session_states_org_user_updated", table_name="session_states")
    op.drop_table("session_states")
