"""Add workspace_id to skills table for multi-tenant isolation.

Revision ID: 009_skill_workspace
Revises: 008_opening_remarks
Create Date: 2026-06-06
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "009_skill_workspace"
down_revision = "008_opening_remarks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add workspace_id column, drop old name index, create composite index."""

    # Add workspace_id with default zero UUID (matches existing data)
    op.add_column(
        "skills",
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            nullable=False,
            server_default=sa.text("'00000000-0000-0000-0000-000000000000'"),
        ),
    )

    # Drop old global unique index on name
    op.drop_index("idx_skills_name", table_name="skills")

    # Create composite unique index (workspace_id, name)
    op.create_index(
        "idx_skills_workspace_name",
        "skills",
        ["workspace_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Remove workspace_id, restore old name index."""

    op.drop_index("idx_skills_workspace_name", table_name="skills")

    op.create_index(
        "idx_skills_name",
        "skills",
        ["name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.drop_column("skills", "workspace_id")
