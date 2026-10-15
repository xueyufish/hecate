"""Add the operator-controlled managed lease posture to standalone enrollments.

Revision ID: c9dae0f1b2a3
Revises: f3a4b5c6d7e8
"""

import sqlalchemy as sa
from alembic import op

revision = "c9dae0f1b2a3"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Existing enrollments start with an empty posture (deny-by-default:
    no adopted policy version → the host refuses protected actions under
    the managed profile)."""
    op.add_column(
        "standalone_enrollments",
        sa.Column("lease_policy", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    """Drop the posture column; previously issued leases expire by TTL."""
    op.drop_column("standalone_enrollments", "lease_policy")
