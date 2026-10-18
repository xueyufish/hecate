"""Add evaluation_runs.evaluation_scope for evidence-coverage checks.

Revision ID: 018_evaluation_scope
Revises: 017_agent_version_eval_binding
Create Date: 2026-10-17

Expand-only: the new column is nullable and never backfilled — runs bound
before scope freezing report unverifiable coverage instead of fabricating a
scope. Downgrade refuses to discard frozen evidence. sa.JSON matches the ORM column type
so the migration-drift gate sees identical types.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "018_evaluation_scope"
down_revision = "017_agent_version_eval_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evaluation_runs", sa.Column("evaluation_scope", sa.JSON(), nullable=True))


def downgrade() -> None:
    populated = (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM evaluation_runs WHERE evaluation_scope IS NOT NULL LIMIT 1"))
        .first()
    )
    if populated is not None:
        raise RuntimeError("Cannot downgrade: frozen evaluation scope evidence must be retained")
    op.drop_column("evaluation_runs", "evaluation_scope")
