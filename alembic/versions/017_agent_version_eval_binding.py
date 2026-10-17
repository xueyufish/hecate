"""Add evaluation_runs.agent_version for agent publish-gate version binding.

Revision ID: 017_agent_version_eval_binding
Revises: 7a6e5d4c3b20
Create Date: 2026-10-17

Expand-only: the new columns are nullable and never backfilled — historical
runs stay unbound (NULL) and never participate in any version's publish
gate. Downgrade is refused once binding evidence exists; application rollback keeps these additive columns.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "017_agent_version_eval_binding"
down_revision = "7a6e5d4c3b20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evaluation_runs", sa.Column("agent_id", sa.Uuid(), nullable=True))
    op.add_column("evaluation_runs", sa.Column("agent_version", sa.Integer(), nullable=True))
    op.add_column("evaluation_runs", sa.Column("agent_config_hash", sa.String(64), nullable=True))
    op.add_column("evaluation_runs", sa.Column("agent_snapshot", sa.JSON(), nullable=True))
    op.create_index("idx_eval_runs_agent_id", "evaluation_runs", ["agent_id"])
    op.create_index("idx_eval_runs_agent_version", "evaluation_runs", ["agent_version"])


def downgrade() -> None:
    populated = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM evaluation_runs WHERE agent_id IS NOT NULL "
                "OR agent_version IS NOT NULL OR agent_config_hash IS NOT NULL "
                "OR agent_snapshot IS NOT NULL LIMIT 1"
            )
        )
        .first()
    )
    if populated is not None:
        raise RuntimeError("Cannot downgrade: version-bound evaluation evidence must be retained")
    op.drop_index("idx_eval_runs_agent_version", table_name="evaluation_runs")
    op.drop_index("idx_eval_runs_agent_id", table_name="evaluation_runs")
    for column in ("agent_snapshot", "agent_config_hash", "agent_version", "agent_id"):
        op.drop_column("evaluation_runs", column)
