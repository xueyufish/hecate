"""add security_findings table

Revision ID: y3e4f5a6b7c8
Revises: x2d3e4f5a6b7
Create Date: 2026-07-25

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "y3e4f5a6b7c8"
down_revision = "x2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create security_findings table."""
    op.create_table(
        "security_findings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("rule_name", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("source_event", sa.JSON(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_security_finding_severity_ts", "security_findings", ["severity", "created_at"])
    op.create_index("ix_security_finding_rule_ts", "security_findings", ["rule_name", "created_at"])


def downgrade() -> None:
    """Drop security_findings table."""
    op.drop_index("ix_security_finding_rule_ts", table_name="security_findings")
    op.drop_index("ix_security_finding_severity_ts", table_name="security_findings")
    op.drop_table("security_findings")
