"""Add agent_publications — append-only publish receipts (step11).

Revision ID: 019_agent_publication_receipt
Revises: 018_evaluation_scope
Create Date: 2026-10-17

Expand-only: a new table, no backfill — publications made before receipts
existed have no record by design. Downgrade refuses to discard publication
evidence: the mutable pointer and general audit cannot reconstruct it.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "019_agent_publication_receipt"
down_revision = "018_evaluation_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_publications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("agent_config_hash", sa.String(64), nullable=False),
        sa.Column("candidate_run_id", sa.Uuid(), nullable=True),
        sa.Column("candidate_run_hash", sa.String(64), nullable=True),
        sa.Column("baseline_run_id", sa.Uuid(), nullable=True),
        sa.Column("baseline_run_hash", sa.String(64), nullable=True),
        sa.Column("gate_config", sa.JSON(), nullable=True),
        sa.Column("gate_report", sa.JSON(), nullable=True),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("operator_user_id", sa.Uuid(), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "outcome IN ('passed', 'warn_passed', 'force_bypassed', 'gate_off')",
            name="ck_agent_publications_outcome",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
    )
    op.create_index("idx_agent_publications_agent", "agent_publications", ["agent_id", "created_at"])
    op.create_index("idx_agent_publications_workspace", "agent_publications", ["workspace_id", "agent_id"])


def downgrade() -> None:
    populated = op.get_bind().execute(sa.text("SELECT 1 FROM agent_publications LIMIT 1")).first()
    if populated is not None:
        raise RuntimeError("Cannot downgrade: agent publication evidence must be retained")
    op.drop_index("idx_agent_publications_workspace", table_name="agent_publications")
    op.drop_index("idx_agent_publications_agent", table_name="agent_publications")
    op.drop_table("agent_publications")
