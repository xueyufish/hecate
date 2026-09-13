"""named dataset versions + run binding + publish evaluation gate (7.3a/7.3b)

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-09-13

Adds (Publish Evaluation Gate + Named Dataset Versions 7.3a/7.3b):
- ``evaluation_dataset_versions`` table — named, immutable freezes of a
  dataset's item set (items + content hash shared with the run-snapshot
  serialization). ``(dataset_id, name)`` is unique across soft-deleted rows
  too, so a name always refers to the same frozen content.
- ``evaluation_runs.dataset_version_id`` — nullable; set when a run is
  pinned to a named version (snapshot and execution both come from the
  version's frozen items).
- ``workflows.evaluation_gate`` — nullable JSON publish-gate configuration
  (``NULL`` = gate off).

All changes are additive; existing rows are unaffected.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: str | None = "e1f2a3b4c5d6"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_dataset_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("items", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "name", name="uq_eval_dataset_versions_name"),
    )
    op.create_index(
        "idx_eval_dataset_versions_dataset",
        "evaluation_dataset_versions",
        ["dataset_id"],
    )
    op.create_index(
        "idx_eval_dataset_versions_workspace",
        "evaluation_dataset_versions",
        ["workspace_id", "deleted"],
    )

    op.add_column("evaluation_runs", sa.Column("dataset_version_id", sa.Uuid(), nullable=True))
    op.create_index("idx_eval_runs_dataset_version", "evaluation_runs", ["dataset_version_id"])

    op.add_column("workflows", sa.Column("evaluation_gate", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("workflows", "evaluation_gate")
    op.drop_index("idx_eval_runs_dataset_version", table_name="evaluation_runs")
    op.drop_column("evaluation_runs", "dataset_version_id")
    op.drop_index("idx_eval_dataset_versions_workspace", table_name="evaluation_dataset_versions")
    op.drop_index("idx_eval_dataset_versions_dataset", table_name="evaluation_dataset_versions")
    op.drop_table("evaluation_dataset_versions")
