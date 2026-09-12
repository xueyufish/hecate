"""add annotation queues, widen evaluation_task_scores for human scores

Revision ID: c8d9e0f1a2b4
Revises: a6b7c8d9e0f1
Create Date: 2026-09-12

Adds (Human Annotation + Human Score Calibration 7.4/7.4a):
- ``annotation_queues`` table — reviewer worklists over production traces
- ``annotation_queue_items`` table — one row per (queue, trace), unique per
  target, status pending → claimed → completed | skipped
- ``evaluation_task_scores`` — ``task_id`` becomes nullable (NULL = human
  annotation row) plus ``annotator_id`` / ``value_label`` /
  ``overrides_score_id`` / ``reason_code`` columns; the idempotency unique
  constraint is replaced by a partial unique index (``WHERE task_id IS NOT
  NULL``) so human rows are exempt
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c8d9e0f1a2b4"
down_revision: str = "a6b7c8d9e0f1"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "annotation_queues",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("metric_defs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("assigned_user_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_annotation_queues_workspace",
        "annotation_queues",
        ["workspace_id", "deleted"],
    )

    op.create_table(
        "annotation_queue_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("queue_id", sa.Uuid(), nullable=False),
        sa.Column("target_type", sa.String(20), nullable=False, server_default="trace"),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("added_by", sa.Uuid(), nullable=True),
        sa.Column("claimed_by", sa.Uuid(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by", sa.Uuid(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "queue_id",
            "target_type",
            "target_id",
            name="uq_annotation_queue_items_target",
        ),
    )
    op.create_index("idx_annotation_queue_items_queue", "annotation_queue_items", ["queue_id"])
    op.create_index(
        "idx_annotation_queue_items_status",
        "annotation_queue_items",
        ["queue_id", "status", "deleted"],
    )
    op.create_index(
        "idx_annotation_queue_items_workspace",
        "annotation_queue_items",
        ["workspace_id", "deleted"],
    )

    op.add_column("evaluation_task_scores", sa.Column("annotator_id", sa.Uuid(), nullable=True))
    op.add_column("evaluation_task_scores", sa.Column("value_label", sa.String(255), nullable=True))
    op.add_column("evaluation_task_scores", sa.Column("overrides_score_id", sa.Uuid(), nullable=True))
    op.add_column("evaluation_task_scores", sa.Column("reason_code", sa.String(50), nullable=True))
    op.alter_column("evaluation_task_scores", "task_id", existing_type=sa.Uuid(), nullable=True)

    op.drop_constraint("uq_eval_task_scores_idempotency", "evaluation_task_scores", type_="unique")
    op.create_index(
        "uq_eval_task_scores_idempotency",
        "evaluation_task_scores",
        ["task_id", "target_type", "target_id", "metric_name"],
        unique=True,
        postgresql_where=sa.text("task_id IS NOT NULL"),
    )
    op.create_index(
        "idx_eval_task_scores_human",
        "evaluation_task_scores",
        ["target_id", "metric_name", "source"],
    )


def downgrade() -> None:
    op.drop_index("idx_eval_task_scores_human", table_name="evaluation_task_scores")
    op.drop_index("uq_eval_task_scores_idempotency", table_name="evaluation_task_scores")
    op.create_unique_constraint(
        "uq_eval_task_scores_idempotency",
        "evaluation_task_scores",
        ["task_id", "target_type", "target_id", "metric_name"],
    )

    op.alter_column("evaluation_task_scores", "task_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_column("evaluation_task_scores", "reason_code")
    op.drop_column("evaluation_task_scores", "overrides_score_id")
    op.drop_column("evaluation_task_scores", "value_label")
    op.drop_column("evaluation_task_scores", "annotator_id")

    op.drop_index("idx_annotation_queue_items_workspace", table_name="annotation_queue_items")
    op.drop_index("idx_annotation_queue_items_status", table_name="annotation_queue_items")
    op.drop_index("idx_annotation_queue_items_queue", table_name="annotation_queue_items")
    op.drop_table("annotation_queue_items")

    op.drop_index("idx_annotation_queues_workspace", table_name="annotation_queues")
    op.drop_table("annotation_queues")
