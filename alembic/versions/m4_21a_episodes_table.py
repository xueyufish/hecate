"""Task Memory: episodes table (4.21 Episode lifecycle).

Revision ID: m4_21a_episodes_table
Revises: l9m0n1o2p3q4
Create Date: 2026-09-22

Adds the ``episodes`` table — one row per user-goal-level task executed by
an agent. Tool calls land in ``actions`` (typed ``TOOL`` events); tool
results land in ``outcomes``. ``closed_at`` is set by
``episode_close(episode_id)`` and is the only gate into the reflection
candidate pool. Reflexive mirror of ``src/hecate/models/task_memory.py``
``EpisodeModel``. Default ``REFLECTION_ENABLED=false`` so the table exists
but is never queried at runtime until a workspace opts in.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "m4_21a_episodes_table"
down_revision = "l9m0n1o2p3q4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the episodes table + indexes."""
    op.create_table(
        "episodes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("task_type", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("situation", sa.Text(), nullable=True),
        sa.Column("intent", sa.Text(), nullable=True),
        sa.Column(
            "actions",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "outcomes",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_episodes_unit_closed",
        "episodes",
        ["workspace_id", "agent_id", "actor_id", "closed_at"],
    )
    op.create_index(
        "idx_episodes_task_type",
        "episodes",
        ["task_type"],
    )


def downgrade() -> None:
    """Reverse the episodes migration."""
    op.drop_index("idx_episodes_task_type", table_name="episodes")
    op.drop_index("idx_episodes_unit_closed", table_name="episodes")
    op.drop_table("episodes")
