"""add workflow_evaluation columns to evaluation_runs (7.3)

Revision ID: a6b7c8d9e0f1
Revises: b8c9d0e1f2a3
Create Date: 2026-09-11

Adds (Workflow Evaluation 7.3) to ``evaluation_runs``:
- ``workflow_id`` — UUID of the workflow under test (nullable, indexed).
  Populated when ``answer_source == "workflow"``.
- ``workflow_version`` — int, locked at run start. ``NULL`` for non-workflow
  runs.
- ``dataset_snapshot`` — JSON snapshot of the dataset's items + content hash,
  captured at run start so regression comparison is meaningful even when
  the dataset is edited between runs.
- ``repetitions`` — int, ``>=1``, how many times each item was executed.
  When ``>1`` the run summary also exposes ``consistency_rate``.

All columns are nullable to keep backward compatibility with existing rows
produced by 7.2c and earlier.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a6b7c8d9e0f1"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add nullable workflow-evaluation columns to evaluation_runs."""
    op.add_column(
        "evaluation_runs",
        sa.Column("workflow_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "idx_eval_runs_workflow",
        "evaluation_runs",
        ["workflow_id"],
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("workflow_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("dataset_snapshot", sa.JSON(), nullable=True),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("repetitions", sa.Integer(), nullable=True, server_default="1"),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("trajectory", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """Drop workflow-evaluation columns and their index."""
    op.drop_column("evaluation_runs", "trajectory")
    op.drop_column("evaluation_runs", "repetitions")
    op.drop_column("evaluation_runs", "dataset_snapshot")
    op.drop_column("evaluation_runs", "workflow_version")
    op.drop_index("idx_eval_runs_workflow", table_name="evaluation_runs")
    op.drop_column("evaluation_runs", "workflow_id")
