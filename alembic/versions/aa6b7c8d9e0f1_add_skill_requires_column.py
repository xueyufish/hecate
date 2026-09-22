"""add skill requires column

Revision ID: aa6b7c8d9e0f1
Revises: l9m0n1o2p3q4
Create Date: 2026-09-22

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "aa6b7c8d9e0f1"
down_revision = "l9m0n1o2p3q4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add requires JSON column to skills table for skill dependency declaration."""
    op.add_column(
        "skills",
        sa.Column(
            "requires",
            sa.JSON(),
            nullable=True,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    """Drop requires column from skills table."""
    op.drop_column("skills", "requires")
