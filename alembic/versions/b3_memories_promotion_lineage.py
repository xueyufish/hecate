"""Memory promotion lineage: promoted_from_id + per-source uniqueness.

Revision ID: b3_memories_promotion_lineage
Revises: m4_25_lifecycle_governance
Create Date: 2026-09-25

Promotion-idempotency follow-up to the 4.25 lifecycle work:

- ``memories.promoted_from_id`` — on a workspace-shared copy created by the
  lifecycle promotion gate, a pointer to the source actor-scoped row. The
  copy previously carried no link to its source, so every sweep that found
  the source still eligible appended another shared copy.
- Partial unique index ``ux_memories_promoted_from`` — one promoted copy
  EVER per source row. The index deliberately has NO ``deleted`` filter:
  a forgotten/withdrawn shared copy must not be silently re-created by a
  later sweep; re-sharing requires a fresh promotion decision.

Fully additive; existing rows have ``promoted_from_id IS NULL`` and are
unaffected.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b3_memories_promotion_lineage"
down_revision = "m4_25_lifecycle_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the promotion-lineage column and its per-source unique index."""
    op.add_column("memories", sa.Column("promoted_from_id", sa.Uuid(), nullable=True))
    op.create_index(
        "ux_memories_promoted_from",
        "memories",
        ["promoted_from_id"],
        unique=True,
        postgresql_where=sa.text("promoted_from_id IS NOT NULL"),
        sqlite_where=sa.text("promoted_from_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Drop the promotion-lineage index and column."""
    op.drop_index("ux_memories_promoted_from", table_name="memories")
    op.drop_column("memories", "promoted_from_id")
