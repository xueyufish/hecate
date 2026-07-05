"""add agent skill_ids column

Revision ID: n2d3e4f5a6b7
Revises: m1c2d3e4f5a6
Create Date: 2026-07-04 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "n2d3e4f5a6b7"
down_revision: str = "m1c2d3e4f5a6"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add skill_ids JSON column to agents table."""
    op.add_column("agents", sa.Column("skill_ids", sa.JSON(), nullable=True, default=list))


def downgrade() -> None:
    """Remove skill_ids column from agents table."""
    op.drop_column("agents", "skill_ids")
