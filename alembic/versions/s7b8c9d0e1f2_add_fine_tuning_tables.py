"""add datasets and fine_tuning_jobs tables

Revision ID: s7b8c9d0e1f2
Revises: r6a7b8c9d0e1
Create Date: 2026-07-06 15:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "s7b8c9d0e1f2"
down_revision: str = "r6a7b8c9d0e1"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(1000), nullable=True),
        sa.Column("format", sa.String(20), nullable=False, server_default="jsonl"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("schema_preview", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("file_storage_url", sa.String(1024), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_datasets_workspace", "datasets", ["workspace_id", "deleted"])
    op.create_index("idx_datasets_name", "datasets", ["name", "workspace_id", "deleted"])

    op.create_table(
        "fine_tuning_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("base_model", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False, server_default="openai"),
        sa.Column("provider_job_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("result_model_id", sa.String(255), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error_message", sa.String(1000), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
    )
    op.create_index("idx_fine_tuning_jobs_dataset", "fine_tuning_jobs", ["dataset_id", "deleted"])
    op.create_index("idx_fine_tuning_jobs_status", "fine_tuning_jobs", ["status", "deleted"])
    op.create_index("idx_fine_tuning_jobs_workspace", "fine_tuning_jobs", ["workspace_id", "deleted"])


def downgrade() -> None:
    op.drop_index("idx_fine_tuning_jobs_workspace", table_name="fine_tuning_jobs")
    op.drop_index("idx_fine_tuning_jobs_status", table_name="fine_tuning_jobs")
    op.drop_index("idx_fine_tuning_jobs_dataset", table_name="fine_tuning_jobs")
    op.drop_table("fine_tuning_jobs")
    op.drop_index("idx_datasets_name", table_name="datasets")
    op.drop_index("idx_datasets_workspace", table_name="datasets")
    op.drop_table("datasets")
