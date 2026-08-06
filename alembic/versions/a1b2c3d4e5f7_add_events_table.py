"""add events table

Revision ID: a1b2c3d4e5f7
Revises: h9c0d1e2f3a4
Create Date: 2026-08-04

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "a1b2c3d4e5f7"
down_revision = "h9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create events table for EventStore PostgreSQL persistence (Change 5)."""
    op.create_table(
        "events",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("superstep", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("node_id", sa.String(length=256), nullable=True),
        sa.Column("trace_id", sa.String(length=256), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("session_id", "version"),
    )
    op.create_index(
        "idx_events_session_version",
        "events",
        ["session_id", "version"],
    )
    op.create_index(
        "idx_events_org_user_created",
        "events",
        ["org_id", "user_id", "created_at"],
    )


def downgrade() -> None:
    """Drop events table."""
    op.drop_index("idx_events_org_user_created", table_name="events")
    op.drop_index("idx_events_session_version", table_name="events")
    op.drop_table("events")
