"""Add evaluation dataset versioning and item assertions.

Revision ID: h9c0d1e2f3a4
Revises: g8b9c0d1e2f3
Create Date: 2026-06-22
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "h9c0d1e2f3a4"
down_revision = "g8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evaluation_datasets", sa.Column("version", sa.String(50), nullable=False, server_default="v1.0"))
    op.add_column("evaluation_datasets", sa.Column("baseline_run_id", sa.UUID(), nullable=True))
    op.add_column(
        "evaluation_datasets", sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.text("false"))
    )
    op.add_column("evaluation_datasets", sa.Column("default_threshold", sa.Float(), nullable=True))
    op.create_index("idx_eval_datasets_baseline", "evaluation_datasets", ["baseline_run_id"])

    op.add_column("evaluation_items", sa.Column("assertions", sa.JSON(), nullable=True))
    op.add_column("evaluation_items", sa.Column("tags", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("evaluation_items", "tags")
    op.drop_column("evaluation_items", "assertions")

    op.drop_index("idx_eval_datasets_baseline", table_name="evaluation_datasets")
    op.drop_column("evaluation_datasets", "default_threshold")
    op.drop_column("evaluation_datasets", "is_locked")
    op.drop_column("evaluation_datasets", "baseline_run_id")
    op.drop_column("evaluation_datasets", "version")
