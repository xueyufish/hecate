"""add feature_flags table

Revision ID: a1b2c3d4e5f6
Revises: z4f5a6b7c8d9
Create Date: 2026-08-07

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "z4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create feature_flags table for runtime feature flag evaluation."""
    op.create_table(
        "feature_flags",
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("targeting_rules", sa.JSON(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("target_removal_version", sa.String(32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("evaluation_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_true_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index("idx_feature_flags_status", "feature_flags", ["status"])


def downgrade() -> None:
    """Drop feature_flags table."""
    op.drop_index("idx_feature_flags_status", table_name="feature_flags")
    op.drop_table("feature_flags")
