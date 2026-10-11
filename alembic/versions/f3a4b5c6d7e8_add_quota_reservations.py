"""add quota reservations table (step7 local slice)

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-10-11

Pure expand: a new per-invocation reservation table for the budget ledger.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f3a4b5c6d7e8"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create quota_reservations."""
    op.create_table(
        "quota_reservations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("invocation_key", sa.String(200), nullable=False),
        sa.Column("resource_type", sa.String(20), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("window_type", sa.String(20), nullable=False),
        sa.Column("units", sa.Float(), nullable=False),
        sa.Column("released", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("settled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("unknown_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("settled_units", sa.Float(), nullable=True),
        sa.Column("usage_status", sa.String(20), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quota_reservations_invocation_key", "quota_reservations", ["invocation_key"])
    op.create_index(
        "ix_quota_reservations_scope",
        "quota_reservations",
        ["resource_type", "scope", "scope_id"],
    )
    # One open reservation per invocation key: concurrent reserves of the same
    # logical call cannot double-hold budget.
    op.create_index(
        "uq_quota_reservations_open",
        "quota_reservations",
        ["invocation_key"],
        unique=True,
        sqlite_where=sa.text("released = 0 AND settled = 0"),
        postgresql_where=sa.text("released = false AND settled = false"),
    )


def downgrade() -> None:
    """Reverse exactly what upgrade added."""
    op.drop_index("uq_quota_reservations_open", table_name="quota_reservations")
    op.drop_index("ix_quota_reservations_scope", table_name="quota_reservations")
    op.drop_index("ix_quota_reservations_invocation_key", table_name="quota_reservations")
    op.drop_table("quota_reservations")
