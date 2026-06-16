"""Add opening_remarks and enable_suggestions columns to agents.

Revision ID: 008_opening_remarks
Revises: 007_workflow_runs
Create Date: 2026-05-31
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "008_opening_remarks"
down_revision = "007_workflow_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add opening_remarks (TEXT nullable) and enable_suggestions (BOOLEAN default true) to agents."""

    op.add_column("agents", sa.Column("opening_remarks", sa.Text(), nullable=True))
    op.add_column(
        "agents",
        sa.Column("enable_suggestions", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
    )


def downgrade() -> None:
    """Remove opening_remarks and enable_suggestions columns from agents."""

    op.drop_column("agents", "enable_suggestions")
    op.drop_column("agents", "opening_remarks")
