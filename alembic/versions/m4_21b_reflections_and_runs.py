"""Task Memory: reflections + reflection_runs + extend consolidation_runs (4.21 + 4.5 link).

Revision ID: m4_21b_reflections_and_runs
Revises: m4_21a_episodes_table
Create Date: 2026-09-22

Adds:

- ``reflections`` — typed reflection records produced by ReflectionEngine.
  Status flow: ``pending → approved | rejected | deprecated``. ``hints``
  hard-capped at ≤ 300 words (enforced at the service layer).
- ``reflection_runs`` — parallel audit table to ``consolidation_runs``;
  same status vocabulary (``running / success / partial / failed``) and
  same advisory-lock plumbing.

Plus an additive column on the existing ``consolidation_runs`` table —
``reflection_type`` — set when a run mixed consolidation and reflection
work in the same unit (default null: pure consolidation). Existing
consolidation behavior is unchanged.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "m4_21b_reflections_and_runs"
down_revision = "m4_21a_episodes_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create reflections + reflection_runs and extend consolidation_runs."""
    op.create_table(
        "reflections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column(
            "use_cases",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("hints", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column(
            "source_episode_ids",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("operator", sa.String(length=20), nullable=False, server_default="add"),
        sa.Column("superseded_by", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("isrel", sa.Float(), nullable=True),
        sa.Column("issup", sa.Float(), nullable=True),
        sa.Column("isuse", sa.Float(), nullable=True),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deprecation_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_reflections_status_use_cases",
        "reflections",
        ["workspace_id", "status"],
    )
    op.create_index(
        "idx_reflections_agent_status",
        "reflections",
        ["workspace_id", "agent_id", "status"],
    )

    op.create_table(
        "reflection_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("trigger", sa.String(length=20), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "candidate_episode_ids",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("adopted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "confidence_distribution",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "rejection_reasons",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="running"),
        sa.Column("llm_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_reflection_runs_unit",
        "reflection_runs",
        ["workspace_id", "agent_id", "created_at"],
    )
    op.create_index(
        "idx_reflection_runs_status",
        "reflection_runs",
        ["status"],
    )

    # Extend consolidation_runs with reflection_type (null = pure consolidation).
    # Existing rows stay null; new reflection-mixed runs set the column.
    op.add_column(
        "consolidation_runs",
        sa.Column("reflection_type", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    """Reverse the reflections + reflection_runs + consolidation_runs extension."""
    op.drop_column("consolidation_runs", "reflection_type")
    op.drop_index("idx_reflection_runs_status", table_name="reflection_runs")
    op.drop_index("idx_reflection_runs_unit", table_name="reflection_runs")
    op.drop_table("reflection_runs")
    op.drop_index("idx_reflections_agent_status", table_name="reflections")
    op.drop_index("idx_reflections_status_use_cases", table_name="reflections")
    op.drop_table("reflections")
