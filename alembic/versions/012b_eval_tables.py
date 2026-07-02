"""Create evaluation and knowledge_memories tables.

Revision ID: 012b_eval_tables
Revises: 012_org_rbac_api_keys
Create Date: 2026-06-09

Creates:
- evaluation_datasets
- evaluation_items
- evaluation_runs
- evaluation_scores
- knowledge_memories

All tables include workspace_id for tenant isolation.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "012b_eval_tables"
down_revision = "012_org_rbac_api_keys"
branch_labels = None
depends_on = None

_DEFAULT_UUID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    # --- evaluation_datasets ---
    op.create_table(
        "evaluation_datasets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False, server_default=_DEFAULT_UUID),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_eval_datasets_created", "evaluation_datasets", ["created_at"])
    op.create_index("idx_eval_datasets_workspace", "evaluation_datasets", ["workspace_id", "deleted"])

    # --- evaluation_items ---
    op.create_table(
        "evaluation_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("expected_answer", sa.Text(), nullable=True),
        sa.Column("generated_answer", sa.Text(), nullable=True),
        sa.Column("context", sa.JSON(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False, server_default=_DEFAULT_UUID),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_eval_items_dataset", "evaluation_items", ["dataset_id"])
    op.create_index("idx_eval_items_workspace", "evaluation_items", ["workspace_id", "deleted"])

    # --- evaluation_runs ---
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("evaluator_configs", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False, server_default=_DEFAULT_UUID),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_eval_runs_dataset", "evaluation_runs", ["dataset_id"])
    op.create_index("idx_eval_runs_workspace", "evaluation_runs", ["workspace_id", "deleted"])

    # --- evaluation_scores ---
    op.create_table(
        "evaluation_scores",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("metric_name", sa.String(100), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="llm_judge"),
        sa.Column("workspace_id", sa.Uuid(), nullable=False, server_default=_DEFAULT_UUID),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_eval_scores_run", "evaluation_scores", ["run_id"])
    op.create_index("idx_eval_scores_item", "evaluation_scores", ["item_id"])
    op.create_index("idx_eval_scores_workspace", "evaluation_scores", ["workspace_id", "deleted"])

    # --- knowledge_memories ---
    op.create_table(
        "knowledge_memories",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False, server_default=_DEFAULT_UUID),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("importance", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("access_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(50), nullable=False, server_default="agent_tool"),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_knowledge_memories_workspace", "knowledge_memories", ["workspace_id", "deleted"])
    op.create_index("idx_knowledge_memories_agent", "knowledge_memories", ["agent_id"])
    op.create_index("idx_knowledge_memories_importance", "knowledge_memories", ["importance"])


def downgrade() -> None:
    op.drop_index("idx_knowledge_memories_importance", table_name="knowledge_memories")
    op.drop_index("idx_knowledge_memories_agent", table_name="knowledge_memories")
    op.drop_index("idx_knowledge_memories_workspace", table_name="knowledge_memories")
    op.drop_table("knowledge_memories")

    op.drop_index("idx_eval_scores_workspace", table_name="evaluation_scores")
    op.drop_index("idx_eval_scores_item", table_name="evaluation_scores")
    op.drop_index("idx_eval_scores_run", table_name="evaluation_scores")
    op.drop_table("evaluation_scores")

    op.drop_index("idx_eval_runs_workspace", table_name="evaluation_runs")
    op.drop_index("idx_eval_runs_dataset", table_name="evaluation_runs")
    op.drop_table("evaluation_runs")

    op.drop_index("idx_eval_items_workspace", table_name="evaluation_items")
    op.drop_index("idx_eval_items_dataset", table_name="evaluation_items")
    op.drop_table("evaluation_items")

    op.drop_index("idx_eval_datasets_workspace", table_name="evaluation_datasets")
    op.drop_index("idx_eval_datasets_created", table_name="evaluation_datasets")
    op.drop_table("evaluation_datasets")
