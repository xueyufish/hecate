"""add prompt optimization tables and prompt version provenance

Revision ID: p19a0b3c4d5e
Revises: e0f1a2b3c4d5
Create Date: 2026-09-15

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "p19a0b3c4d5e"
down_revision = "e0f1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create prompt_optimization_runs, prompt_optimization_candidates, prompt_versions.metadata."""
    op.create_table(
        "prompt_optimization_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("prompt_id", sa.Uuid(), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column(
            "dataset_version_id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column("dataset_version_hash", sa.String(64), nullable=False),
        sa.Column("split_ratio", sa.Float(), nullable=False),
        sa.Column("evaluator_configs", sa.JSON(), nullable=True),
        sa.Column("primary_metric", sa.String(100), nullable=False),
        sa.Column("min_improvement", sa.Float(), nullable=False),
        sa.Column("max_regression", sa.Float(), nullable=False),
        sa.Column("budget_preset", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("max_rounds", sa.Integer(), nullable=False),
        sa.Column("mutation_call_limit", sa.Integer(), nullable=False),
        sa.Column("rollout_item_limit", sa.Integer(), nullable=False),
        sa.Column("strategy", sa.String(64), nullable=False, server_default="reflective_mutation"),
        sa.Column("strategy_params", sa.JSON(), nullable=True),
        sa.Column("reflection_model", sa.String(255), nullable=False, server_default="gpt-4o-mini"),
        sa.Column("status", sa.String(30), nullable=False, server_default="created"),
        sa.Column("stop_reason", sa.String(30), nullable=True),
        sa.Column("round_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["dataset_version_id"], ["evaluation_dataset_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_prompt_opt_runs_prompt", "prompt_optimization_runs", ["prompt_id", "status"])
    op.create_index("idx_prompt_opt_runs_workspace", "prompt_optimization_runs", ["workspace_id", "deleted"])

    op.create_table(
        "prompt_optimization_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("round_no", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parent_candidate_id", sa.Uuid(), nullable=True),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="rejected_mutation"),
        sa.Column("gate_report", sa.JSON(), nullable=True),
        sa.Column("per_item_results", sa.JSON(), nullable=True),
        sa.Column("reflection_summary", sa.Text(), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("rollout_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["prompt_optimization_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_prompt_opt_cand_run", "prompt_optimization_candidates", ["run_id", "status"])
    op.create_index("idx_prompt_opt_cand_workspace", "prompt_optimization_candidates", ["workspace_id", "deleted"])

    op.add_column("prompt_versions", sa.Column("metadata", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Drop prompt optimization tables and the prompt version provenance column."""
    op.drop_column("prompt_versions", "metadata")
    op.drop_index("idx_prompt_opt_cand_workspace", table_name="prompt_optimization_candidates")
    op.drop_index("idx_prompt_opt_cand_run", table_name="prompt_optimization_candidates")
    op.drop_table("prompt_optimization_candidates")
    op.drop_index("idx_prompt_opt_runs_workspace", table_name="prompt_optimization_runs")
    op.drop_index("idx_prompt_opt_runs_prompt", table_name="prompt_optimization_runs")
    op.drop_table("prompt_optimization_runs")
