"""Add the operator-controlled managed scope to standalone enrollments.

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
"""

import sqlalchemy as sa
from alembic import op

revision = "e6f7a8b9c0d1"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Existing enrollments start with an empty scope (deny-by-default)."""
    op.add_column(
        "standalone_enrollments",
        sa.Column("managed_scope", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    """Drop the scope column; previously issued leases expire by TTL."""
    op.drop_column("standalone_enrollments", "managed_scope")
