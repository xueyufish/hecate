"""Drop the checkpoints table (13.4a-7 / C2 cleanup).

The checkpoints table backs ``PostgresCheckpointStore`` and
``AgentStateStore``, both deprecated as of 1.3.19 and removed
in this change. The materialized-cache checkpoint role is now
served by ``SessionStateStore`` (Redis / PostgreSQL / Tiered) and
the event log is the source of truth for execution state. No
remaining production code reads or writes this table.

The 000_base migration still creates it on upgrade (backward
compatibility with existing deployments); this new migration drops
it forward-only. Idempotent: a re-run on a deployment that already
applied this drop is a no-op (IF EXISTS).
"""

from __future__ import annotations

from alembic import op

revision = "c5d6e7f8a9b0"
down_revision = "8d4e5f6a7b8c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_checkpoints_session")
    op.execute("DROP TABLE IF EXISTS checkpoints")


def downgrade() -> None:
    """Recreate the original table shape (backward compat for
    re-running an older migration chain). Mirrors the create in
    000_base.py:141-153 verbatim.
    """
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS checkpoints (
            id UUID PRIMARY KEY,
            session_id UUID NOT NULL,
            superstep INTEGER NOT NULL,
            channel_state TEXT NOT NULL,
            metadata_ TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_checkpoints_session ON checkpoints (session_id, superstep)")
