"""add tags column to evaluation_items + dataset_synthesis_jobs table

Revision ID: a7b8c9d0e1f2
Revises: z4f5a6b7c8d9
Create Date: 2026-09-10

Adds:
- ``evaluation_items.tags`` JSONB column (default '[]') for synthesized
  item provenance (used by AI-Synthesized Evaluation Dataset 7.2b)
- ``dataset_synthesis_jobs`` table for tracking async synthesis jobs
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str = "z4f5a6b7c8d9"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "evaluation_items",
        sa.Column(
            "tags",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )

    op.create_table(
        "dataset_synthesis_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("target_dataset_id", sa.Uuid(), nullable=True),
        sa.Column("strategy", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error_message", sa.String(2000), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["target_dataset_id"], ["evaluation_datasets.id"]),
    )
    op.create_index(
        "idx_dataset_synthesis_jobs_status",
        "dataset_synthesis_jobs",
        ["status", "deleted"],
    )
    op.create_index(
        "idx_dataset_synthesis_jobs_workspace",
        "dataset_synthesis_jobs",
        ["workspace_id", "deleted"],
    )
    op.create_index(
        "idx_dataset_synthesis_jobs_target_dataset",
        "dataset_synthesis_jobs",
        ["target_dataset_id", "deleted"],
    )


def downgrade() -> None:
    op.drop_index("idx_dataset_synthesis_jobs_target_dataset", table_name="dataset_synthesis_jobs")
    op.drop_index("idx_dataset_synthesis_jobs_workspace", table_name="dataset_synthesis_jobs")
    op.drop_index("idx_dataset_synthesis_jobs_status", table_name="dataset_synthesis_jobs")
    op.drop_table("dataset_synthesis_jobs")

    op.drop_column("evaluation_items", "tags")
