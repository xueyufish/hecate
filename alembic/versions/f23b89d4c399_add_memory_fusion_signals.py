"""add memory fusion retrieval signal columns

Revision ID: f23b89d4c399
Revises: a8b9c0d1e2f4
Create Date: 2026-09-21

Adds the memory-importance-fusion signal surface:

- ``last_confirmed_at`` on L3/L4 memories — exogenous decay anchor for
  retrieval-time time-decay ranking (insert time, refreshed only by
  consolidation UPDATE / SUPERSEDE; backfilled from ``created_at``).
- ``embedding_real`` — marks rows whose ``embedding`` is a real model
  vector; legacy mock vectors are excluded from cosine scoring.
- ``last_accessed_at`` — retrieval-hit heat clock for the offline value
  score (never the decay anchor).
- ``value_score`` / ``value_components`` — consolidation-maintained offline
  memory value score and its named components (observability only).
- ``memory_access_sessions`` — distinct-session access markers backing the
  deduplicated access-frequency signal.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "f23b89d4c399"
down_revision = "a8b9c0d1e2f4"
branch_labels = None
depends_on = None

_MEMORY_TABLES = ("memories", "knowledge_memories")


def upgrade() -> None:
    """Add fusion signal columns, backfill anchors, create access table."""
    for table in _MEMORY_TABLES:
        op.add_column(table, sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(table, sa.Column("embedding_real", sa.Boolean(), nullable=False, server_default=sa.false()))
        op.add_column(table, sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(table, sa.Column("value_score", sa.Float(), nullable=True))
        op.add_column(table, sa.Column("value_components", sa.JSON(), nullable=True))
        op.execute(f"UPDATE {table} SET last_confirmed_at = created_at WHERE last_confirmed_at IS NULL")

    op.create_table(
        "memory_access_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("target_type", sa.String(30), nullable=False),
        sa.Column("memory_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "target_type",
            "memory_id",
            "session_id",
            name="uq_memory_access_sessions_hit",
        ),
    )
    op.create_index(
        "idx_memory_access_sessions_memory",
        "memory_access_sessions",
        ["target_type", "memory_id"],
    )
    op.create_index(
        "idx_memory_access_sessions_workspace",
        "memory_access_sessions",
        ["workspace_id", "deleted"],
    )


def downgrade() -> None:
    """Drop the fusion signal surface."""
    op.drop_index("idx_memory_access_sessions_workspace", table_name="memory_access_sessions")
    op.drop_index("idx_memory_access_sessions_memory", table_name="memory_access_sessions")
    op.drop_table("memory_access_sessions")
    for table in _MEMORY_TABLES:
        op.drop_column(table, "value_components")
        op.drop_column(table, "value_score")
        op.drop_column(table, "last_accessed_at")
        op.drop_column(table, "embedding_real")
        op.drop_column(table, "last_confirmed_at")
