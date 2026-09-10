"""add evaluation_tasks + evaluation_task_scores, extend evaluation_runs

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-09-10

Adds (Online/Offline Evaluation Tasks 7.2c):
- ``evaluation_tasks`` table — persistent task definitions (offline batch /
  online sampler)
- ``evaluation_task_scores`` table — target-typed scores from online tasks,
  idempotent on (task_id, target_type, target_id, metric_name)
- ``evaluation_runs.task_id`` / ``evaluation_runs.summary`` — task-linked runs
- ``traces (type, status, created_at)`` index — online scanner watermark scan
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str = "a7b8c9d0e1f2"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_type", sa.String(20), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("evaluator_configs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("last_scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_eval_tasks_type_status",
        "evaluation_tasks",
        ["task_type", "status", "deleted"],
    )
    op.create_index(
        "idx_eval_tasks_workspace",
        "evaluation_tasks",
        ["workspace_id", "deleted"],
    )

    op.create_table(
        "evaluation_task_scores",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("target_type", sa.String(20), nullable=False, server_default="trace"),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("metric_name", sa.String(100), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="llm_judge"),
        sa.Column("status", sa.String(20), nullable=False, server_default="completed"),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id",
            "target_type",
            "target_id",
            "metric_name",
            name="uq_eval_task_scores_idempotency",
        ),
    )
    op.create_index("idx_eval_task_scores_task", "evaluation_task_scores", ["task_id"])
    op.create_index("idx_eval_task_scores_target", "evaluation_task_scores", ["target_id"])
    op.create_index("idx_eval_task_scores_session", "evaluation_task_scores", ["session_id"])
    op.create_index(
        "idx_eval_task_scores_workspace",
        "evaluation_task_scores",
        ["workspace_id", "deleted"],
    )

    op.add_column("evaluation_runs", sa.Column("task_id", sa.Uuid(), nullable=True))
    op.create_index("idx_eval_runs_task", "evaluation_runs", ["task_id"])
    op.add_column("evaluation_runs", sa.Column("summary", sa.JSON(), nullable=True))

    op.create_index(
        "idx_traces_type_status_created",
        "traces",
        ["type", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_traces_type_status_created", table_name="traces")

    op.drop_column("evaluation_runs", "summary")
    op.drop_index("idx_eval_runs_task", table_name="evaluation_runs")
    op.drop_column("evaluation_runs", "task_id")

    op.drop_index("idx_eval_task_scores_workspace", table_name="evaluation_task_scores")
    op.drop_index("idx_eval_task_scores_session", table_name="evaluation_task_scores")
    op.drop_index("idx_eval_task_scores_target", table_name="evaluation_task_scores")
    op.drop_index("idx_eval_task_scores_task", table_name="evaluation_task_scores")
    op.drop_table("evaluation_task_scores")

    op.drop_index("idx_eval_tasks_workspace", table_name="evaluation_tasks")
    op.drop_index("idx_eval_tasks_type_status", table_name="evaluation_tasks")
    op.drop_table("evaluation_tasks")
