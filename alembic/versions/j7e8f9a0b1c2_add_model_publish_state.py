"""add model publish state columns

Revision ID: j7e8f9a0b1c2
Revises: i5d6e7f8a9b0
Create Date: 2026-09-14

Model service publishing (6.47): `model_registry` gains an explicit publish
flag and the test-evidence timestamp that gates publishing. Existing rows are
backfilled to published so the /v1/models reference surface is unchanged
across the upgrade.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "j7e8f9a0b1c2"
down_revision = "i5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add publish columns and backfill existing rows to published."""
    op.add_column(
        "model_registry",
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "model_registry",
        sa.Column("last_test_passed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Compatibility contract: the /v1/models result set must not shrink across
    # the upgrade, so every pre-existing model stays application-referenceable.
    op.execute("UPDATE model_registry SET is_published = TRUE WHERE NOT deleted")


def downgrade() -> None:
    """Drop publish columns; reference surface returns to unfiltered."""
    op.drop_column("model_registry", "last_test_passed_at")
    op.drop_column("model_registry", "is_published")
