"""Task Memory: Work Context Graph (KM6 / ADR-024 §6) — nodes + edges.

Revision ID: m4_21c_work_context_graph
Revises: m4_21b_reflections_and_runs
Create Date: 2026-09-22

Adds the graph storage for the self-improving work memory layer:

- ``work_context_nodes`` — five node types
  (``method / outcome / correction / source / pattern``).
  ``success_rate`` / ``usage_count`` / ``user_correction_count`` are
  recomputed by a background aggregation job, never on the read path.
- ``work_context_edges`` — four edge types
  (``tried_before / led_to / corrected_by / validated_by``). Both
  endpoints must point at active nodes at insertion time; the service
  layer double-checks the referential invariant before committing.

Default ``REFLECTION_ENABLED=false`` so the tables exist but are never
queried at runtime until a workspace opts in.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "m4_21c_work_context_graph"
down_revision = "m4_21b_reflections_and_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create work_context_nodes + work_context_edges + indexes."""
    op.create_table(
        "work_context_nodes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("node_type", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("success_rate", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("usage_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_correction_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_reliability", sa.Float(), nullable=True),
        sa.Column("linked_reflection_id", sa.Uuid(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_work_context_nodes_type_active",
        "work_context_nodes",
        ["workspace_id", "node_type", "active"],
    )
    op.create_index(
        "idx_work_context_nodes_agent",
        "work_context_nodes",
        ["workspace_id", "agent_id"],
    )

    op.create_table(
        "work_context_edges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("source_node_id", sa.Uuid(), nullable=False),
        sa.Column("target_node_id", sa.Uuid(), nullable=False),
        sa.Column("edge_type", sa.String(length=30), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_work_context_edges_source",
        "work_context_edges",
        ["workspace_id", "source_node_id"],
    )
    op.create_index(
        "idx_work_context_edges_target",
        "work_context_edges",
        ["workspace_id", "target_node_id"],
    )


def downgrade() -> None:
    """Reverse the Work Context Graph migration."""
    op.drop_index("idx_work_context_edges_target", table_name="work_context_edges")
    op.drop_index("idx_work_context_edges_source", table_name="work_context_edges")
    op.drop_table("work_context_edges")
    op.drop_index("idx_work_context_nodes_agent", table_name="work_context_nodes")
    op.drop_index("idx_work_context_nodes_type_active", table_name="work_context_nodes")
    op.drop_table("work_context_nodes")
