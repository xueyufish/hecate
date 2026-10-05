"""Add per-event outbox receipts to survive commit reordering.

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
"""

import sqlalchemy as sa
from alembic import op

revision = "d5e6f7a8b9c0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Replay historical events through the idempotent projector once."""
    op.create_table(
        "durable_outbox_receipt",
        sa.Column("relay_key", sa.String(128), primary_key=True),
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.String(64), nullable=False),
    )


def downgrade() -> None:
    """Remove derived acknowledgments; authoritative events remain intact."""
    op.drop_table("durable_outbox_receipt")
