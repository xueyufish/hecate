"""memory consolidation: runs/pressure-flag tables, superseded_by columns

Revision ID: a8b9c0d1e2f4
Revises: d7e8f9a0b1c2
Create Date: 2026-09-20

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "a8b9c0d1e2f4"
down_revision = "d7e8f9a0b1c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add consolidation audit/flag tables and SUPERSEDE lineage columns."""
    # SUPERSEDE lineage: pointer from a superseded memory to its successor.
    # Rows are soft-deleted but retained (supersede, never delete).
    op.add_column("memories", sa.Column("superseded_by", sa.Uuid(), nullable=True))
    op.add_column("knowledge_memories", sa.Column("superseded_by", sa.Uuid(), nullable=True))

    # Run-level audit for sleep-time consolidation. Doubles as the per-unit
    # watermark source: the latest SUCCESS run's window_end bounds the next
    # review window.
    op.create_table(
        "consolidation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("adopted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("llm_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("degraded", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("operations", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_consolidation_runs_workspace",
        "consolidation_runs",
        ["workspace_id", "deleted"],
    )
    op.create_index(
        "idx_consolidation_runs_unit",
        "consolidation_runs",
        ["workspace_id", "agent_id", "status", "created_at"],
    )

    # Pending pressure markers: written by the memory pressure alert on
    # threshold crossing, consumed by the consolidation trigger bus. The
    # zero-UUID user_id sentinel denotes the agent-level unit (SQL NULLs
    # would be distinct under the unique constraint).
    op.create_table(
        "consolidation_pressure_flags",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "agent_id",
            "user_id",
            name="uq_consolidation_pressure_flags_unit",
        ),
    )
    op.create_index(
        "idx_consolidation_pressure_flags_unit",
        "consolidation_pressure_flags",
        ["workspace_id", "agent_id"],
    )


def downgrade() -> None:
    """Reverse the consolidation migration."""
    op.drop_index("idx_consolidation_pressure_flags_unit", "consolidation_pressure_flags")
    op.drop_table("consolidation_pressure_flags")

    op.drop_index("idx_consolidation_runs_unit", "consolidation_runs")
    op.drop_index("idx_consolidation_runs_workspace", "consolidation_runs")
    op.drop_table("consolidation_runs")

    op.drop_column("knowledge_memories", "superseded_by")
    op.drop_column("memories", "superseded_by")
