"""Memory lifecycle + governance: policies, flush windows, archived_at, edit-log reason.

Revision ID: m4_25_lifecycle_governance
Revises: m4_23_namespace_team_actor
Create Date: 2026-09-23

Fully additive schema for the memory-lifecycle-governance change:

- ``memory_policies`` — per-workspace / per-agent governance policy rows
  (``agent_id`` uses the zero-UUID sentinel for the workspace level, so a
  plain unique constraint keeps one row per scope).
- ``consolidation_flush_windows`` — pending pre-compaction flush windows
  written at the L2 compaction boundary, consumed by the trigger bus.
- ``memories`` / ``knowledge_memories`` gain ``archived_at`` (lifecycle
  soft-delete marker) plus a ``(workspace_id, archived_at)`` index.
- ``memory_edit_log`` gains ``reason`` (lifecycle operation cause).
- Backfill: ``last_confirmed_at`` null rows get ``updated_at`` as the
  decay/TTL anchor (mirrors the fusion-signal backfill precedent).

No existing column is dropped or rewritten; with the feature flags off the
platform behavior is unchanged.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "m4_25_lifecycle_governance"
down_revision = "m4_23_namespace_team_actor"
branch_labels = None
depends_on = None

_ZERO_UUID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    """Create policy/flush tables, archived/reason columns, backfill anchors."""
    op.create_table(
        "memory_policies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            nullable=False,
            server_default=_ZERO_UUID,
        ),
        sa.Column(
            "agent_id",
            sa.Uuid(),
            nullable=False,
            server_default=_ZERO_UUID,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("tool_subset", sa.JSON(), nullable=True),
        sa.Column("sharing_ceiling", sa.String(length=20), nullable=True),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("workspace_id", "agent_id", name="uq_memory_policies_scope"),
    )
    op.create_index(
        "idx_memory_policies_workspace",
        "memory_policies",
        ["workspace_id", "deleted"],
    )

    op.create_table(
        "consolidation_flush_windows",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            nullable=False,
            server_default=_ZERO_UUID,
        ),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column(
            "user_id",
            sa.Uuid(),
            nullable=False,
            server_default=_ZERO_UUID,
        ),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_consolidation_flush_windows_unit",
        "consolidation_flush_windows",
        ["workspace_id", "agent_id", "user_id"],
    )
    op.create_index(
        "idx_consolidation_flush_windows_window",
        "consolidation_flush_windows",
        ["window_end"],
    )

    op.add_column("memories", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "knowledge_memories",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_memories_archived",
        "memories",
        ["workspace_id", "archived_at"],
    )
    op.create_index(
        "idx_knowledge_memories_archived",
        "knowledge_memories",
        ["workspace_id", "archived_at"],
    )

    op.add_column("memory_edit_log", sa.Column("reason", sa.String(length=100), nullable=True))

    # Backfill the decay/TTL anchor: rows consolidated before the fusion
    # change carry a null ``last_confirmed_at``; ``updated_at`` is the best
    # available confirmation proxy for them (same intent as the fusion
    # backfill). Savepoint so a failure here can be rolled back
    # independently of the DDL above.
    bind = op.get_bind()
    with bind.begin_nested() as sp:
        bind.execute(
            sa.text(
                "UPDATE memories SET last_confirmed_at = updated_at "
                "WHERE last_confirmed_at IS NULL"
            )
        )
        bind.execute(
            sa.text(
                "UPDATE knowledge_memories SET last_confirmed_at = updated_at "
                "WHERE last_confirmed_at IS NULL"
            )
        )
        _ = sp


def downgrade() -> None:
    """Reverse the lifecycle/governance schema additions."""
    op.drop_column("memory_edit_log", "reason")
    op.drop_index("idx_knowledge_memories_archived", table_name="knowledge_memories")
    op.drop_index("idx_memories_archived", table_name="memories")
    op.drop_column("knowledge_memories", "archived_at")
    op.drop_column("memories", "archived_at")
    op.drop_index("idx_consolidation_flush_windows_window", table_name="consolidation_flush_windows")
    op.drop_index("idx_consolidation_flush_windows_unit", table_name="consolidation_flush_windows")
    op.drop_table("consolidation_flush_windows")
    op.drop_index("idx_memory_policies_workspace", table_name="memory_policies")
    op.drop_table("memory_policies")
