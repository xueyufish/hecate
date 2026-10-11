"""add governance authority tables (step7 local slice)

Revision ID: e2f3a4b5c6d7
Revises: e6f7a8b9c0d1
Create Date: 2026-10-11

Adds the authoritative approval-binding table and the action-claim table for
ledger-less dispatch paths, plus projection columns on ``approval_records``.
Pure expand: no existing column is altered or removed; downgrade reverses
exactly what upgrade added.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e2f3a4b5c6d7"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create approval_authority + action_claims; extend approval_records."""
    op.create_table(
        "approval_authority",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("approval_ref", sa.String(128), nullable=False),
        sa.Column("principal_fingerprint", sa.String(64), nullable=False),
        sa.Column("tool_name", sa.String(255), nullable=False),
        sa.Column("arguments_digest", sa.String(64), nullable=False),
        sa.Column("target_resources", sa.JSON(), nullable=True),
        sa.Column("deployment_id", sa.String(128), nullable=True),
        sa.Column("domains", sa.JSON(), nullable=True),
        sa.Column("task_scope", sa.String(255), nullable=True),
        sa.Column("anchor_wall", sa.DateTime(timezone=True), nullable=True),
        sa.Column("anchor_monotonic", sa.Float(), nullable=True),
        sa.Column("clock_epoch", sa.String(64), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("one_time", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("judgment", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.String(255), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("consumed_by_invocation_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("approval_ref", name="uq_approval_authority_ref"),
    )
    op.create_index(
        "idx_approval_authority_tool",
        "approval_authority",
        ["tool_name", "judgment", "revoked"],
    )

    op.create_table(
        "action_claims",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("invocation_key", sa.String(200), nullable=False),
        sa.Column("tool_name", sa.String(255), nullable=False),
        sa.Column("arguments_digest", sa.String(64), nullable=False),
        sa.Column("principal_fingerprint", sa.String(64), nullable=False),
        sa.Column("side_effect_class", sa.String(40), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("succeeded", sa.Boolean(), nullable=True),
        sa.Column("result_digest", sa.String(64), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_action_claims_invocation_key", "action_claims", ["invocation_key"])
    # Keep invocation identity unique after completion as well: a delayed
    # contender must replay the existing receipt instead of claiming again.
    op.create_index(
        "uq_action_claims_open",
        "action_claims",
        ["invocation_key"],
        unique=True,
    )

    op.add_column("approval_records", sa.Column("approval_ref", sa.String(128), nullable=True))
    op.add_column("approval_records", sa.Column("arguments_digest", sa.String(64), nullable=True))
    op.add_column("approval_records", sa.Column("target_resources", sa.JSON(), nullable=True))
    op.add_column("approval_records", sa.Column("decision_source", sa.String(32), nullable=True))


def downgrade() -> None:
    """Reverse exactly what upgrade added."""
    op.drop_column("approval_records", "decision_source")
    op.drop_column("approval_records", "target_resources")
    op.drop_column("approval_records", "arguments_digest")
    op.drop_column("approval_records", "approval_ref")
    op.drop_index("uq_action_claims_open", table_name="action_claims")
    op.drop_index("ix_action_claims_invocation_key", table_name="action_claims")
    op.drop_table("action_claims")
    op.drop_index("idx_approval_authority_tool", table_name="approval_authority")
    op.drop_table("approval_authority")
