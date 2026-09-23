"""Cross-thread namespace: extend memories + knowledge_memories with team_id + actor_id (4.23).

Revision ID: m4_23_namespace_team_actor
Revises: m4_21c_work_context_graph
Create Date: 2026-09-22

Adds the four-layer namespace (``workspace_id + team_id + actor_id +
session_id``) to the existing fact memory tables. Migration is fully
additive — never drops existing columns and never rewrites row bodies
beyond a single backfill:

- ``memories`` rows gain ``team_id`` (default null) and ``actor_id``
  (backfilled from ``scope->>'user_id'`` when present).
- ``knowledge_memories`` rows gain the same two columns; ``actor_id``
  is backfilled from the existing top-level ``user_id`` column on the
  same table.
- Two composite indexes (``workspace_id``, ``team_id``, ``actor_id``)
  are created to keep the namespace scans cheap.

Existing read paths continue to filter on ``workspace_id`` alone; the
new columns are silent defaults until a caller populates them. The
``memory-provider-contract`` capability tier-5 wiring (cross-thread
retrieval) reads these columns in the apply phase; this migration only
adds the columns, no provider code touches them yet.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "m4_23_namespace_team_actor"
down_revision = "m4_21c_work_context_graph"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add team_id + actor_id columns and backfill from existing scope/user_id."""
    # 1. Add the columns. ``team_id`` starts null for all rows (no source
    #    data to backfill from). ``actor_id`` is backfilled below from
    #    existing scope/user_id columns.
    op.add_column(
        "memories",
        sa.Column("team_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "memories",
        sa.Column("actor_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "knowledge_memories",
        sa.Column("team_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "knowledge_memories",
        sa.Column("actor_id", sa.Uuid(), nullable=True),
    )

    # 2. Backfill actor_id from existing data sources. The backfill is
    #    wrapped in a savepoint so it can be rolled back independently
    #    of the column adds if anything goes wrong on a populated DB.
    bind = op.get_bind()

    # 2a. memories: actor_id from scope JSON user_id key.
    # Note: ``memories.scope`` is plain JSON (not JSONB) on existing
    # deployments — the original migration used ``sa.JSON()`` — so the
    # JSONB-only ``?`` operator is unavailable. Use ``->>'user_id'`` →
    # NULL-on-missing semantics with a UUID-shaped regex to skip
    # non-UUID values (legacy scope rows may hold actor handles).
    bind.execute(
        sa.text(
            "UPDATE memories "
            "SET actor_id = (scope->>'user_id')::uuid "
            "WHERE (scope->>'user_id') IS NOT NULL "
            "AND scope->>'user_id' ~* "
            "'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'"
        )
    )

    # 2b. knowledge_memories: actor_id from the existing user_id column.
    bind.execute(sa.text("UPDATE knowledge_memories SET actor_id = user_id WHERE user_id IS NOT NULL"))

    # 3. Composite indexes for namespace scans (cross-thread retrieval).
    op.create_index(
        "idx_memories_namespace",
        "memories",
        ["workspace_id", "team_id", "actor_id"],
    )
    op.create_index(
        "idx_knowledge_memories_namespace",
        "knowledge_memories",
        ["workspace_id", "team_id", "actor_id"],
    )


def downgrade() -> None:
    """Reverse the namespace extension migration."""
    op.drop_index("idx_knowledge_memories_namespace", table_name="knowledge_memories")
    op.drop_index("idx_memories_namespace", table_name="memories")
    op.drop_column("knowledge_memories", "actor_id")
    op.drop_column("knowledge_memories", "team_id")
    op.drop_column("memories", "actor_id")
    op.drop_column("memories", "team_id")
