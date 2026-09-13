"""add known-bad exemption columns to evaluation_items (7.3c)

Revision ID: e1f2a3b4c5d6
Revises: d9e0f1a2b3c5
Create Date: 2026-09-12

Adds (Known-bad Exemption 7.3c) to ``evaluation_items``:
- ``known_bad`` — bool, ``false`` by default. Known-bad items still execute
  and record scores, but run aggregation excludes them from pass_rate /
  consistency_rate / metric averages.
- ``known_bad_reason`` — why the item is exempt; required by the service
  layer when marking.
- ``known_bad_marked_by`` / ``known_bad_marked_at`` — audit provenance,
  filled server-side.
- ``known_bad_expires_at`` — reserved for future expiry-based re-review;
  nothing reads it yet.

Existing rows are unaffected: the marker columns default to unmarked.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e1f2a3b4c5d6"
down_revision = "d9e0f1a2b3c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add known-bad exemption columns to evaluation_items."""
    op.add_column(
        "evaluation_items",
        sa.Column("known_bad", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "evaluation_items",
        sa.Column("known_bad_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "evaluation_items",
        sa.Column("known_bad_marked_by", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "evaluation_items",
        sa.Column("known_bad_marked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "evaluation_items",
        sa.Column("known_bad_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Drop known-bad exemption columns."""
    op.drop_column("evaluation_items", "known_bad_expires_at")
    op.drop_column("evaluation_items", "known_bad_marked_at")
    op.drop_column("evaluation_items", "known_bad_marked_by")
    op.drop_column("evaluation_items", "known_bad_reason")
    op.drop_column("evaluation_items", "known_bad")
