"""add evolution loop tables

Revision ID: b7c8d9e0f1a2
Revises: j7e8f9a0b1c2
Create Date: 2026-09-15

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "aa11bb22cc33"
down_revision = "j7e8f9a0b1c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create evolution_runs, evolution_learning_inputs, skill_candidates."""
    op.create_table(
        "evolution_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("input_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("llm_calls_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_evolution_runs_workspace_status", "evolution_runs", ["workspace_id", "status"])

    op.create_table(
        "evolution_learning_inputs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("signal_type", sa.String(40), nullable=False),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column(
            "run_id",
            sa.Uuid(),
            nullable=True,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["evolution_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_evolution_inputs_workspace_status",
        "evolution_learning_inputs",
        ["workspace_id", "status"],
    )

    op.create_table(
        "skill_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("procedure", sa.Text(), nullable=False),
        sa.Column("guardrails", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("failure_category", sa.String(50), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("deltas", sa.JSON(), nullable=True),
        sa.Column("validation_report", sa.JSON(), nullable=True),
        sa.Column("scan_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("scan_detail", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("review_decision", sa.String(30), nullable=True),
        sa.Column("reviewed_by", sa.String(255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_comment", sa.Text(), nullable=True),
        sa.Column("edit_diff", sa.JSON(), nullable=True),
        sa.Column("published_skill_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["evolution_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["published_skill_id"], ["skills.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_skill_candidates_workspace_status", "skill_candidates", ["workspace_id", "status"])
    op.create_index("idx_skill_candidates_workspace_name", "skill_candidates", ["workspace_id", "name"])


def downgrade() -> None:
    """Drop evolution loop tables."""
    op.drop_index("idx_skill_candidates_workspace_name", table_name="skill_candidates")
    op.drop_index("idx_skill_candidates_workspace_status", table_name="skill_candidates")
    op.drop_table("skill_candidates")
    op.drop_index("idx_evolution_inputs_workspace_status", table_name="evolution_learning_inputs")
    op.drop_table("evolution_learning_inputs")
    op.drop_index("idx_evolution_runs_workspace_status", table_name="evolution_runs")
    op.drop_table("evolution_runs")
