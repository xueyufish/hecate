"""add skill learned provenance columns

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-09-15

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "c8d9e0f1a2b3"
down_revision = "aa11bb22cc33"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add learned-skill provenance columns to skills."""
    op.add_column("skills", sa.Column("learned_run_id", sa.Uuid(), nullable=True))
    op.add_column("skills", sa.Column("failure_category", sa.String(50), nullable=True))


def downgrade() -> None:
    """Remove learned-skill provenance columns."""
    op.drop_column("skills", "failure_category")
    op.drop_column("skills", "learned_run_id")
