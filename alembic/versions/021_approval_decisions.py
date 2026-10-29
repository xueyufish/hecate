"""Add the approval-decision operation table for human approval closes.

Revision ID: 021_approval_decisions
Revises: 020_durable_input_revision

Expand-only: no existing table is touched. One row per human decision
operation; ``operation_id`` is unique so concurrent or replayed
submissions arbitrate on it, and ``resume_command_id`` stays NULL until
the resume phase completes — the named recovery consumer re-issues the
deterministic resume from this row. The binding's judgment in
``approval_authority`` remains the decision authority; this table is the
operation/audit record, never a second judgment source.
"""

import sqlalchemy as sa
from alembic import op

revision = "021_approval_decisions"
down_revision = "020_durable_input_revision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add approval-decision storage without altering existing tables."""
    op.create_table(
        "approval_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.String(length=200), nullable=False),
        sa.Column("approval_ref", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.String(length=255), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("wait_token", sa.String(length=256), nullable=False),
        sa.Column("expected_revision", sa.Integer(), nullable=True),
        sa.Column("resume_command_id", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_decisions")),
        sa.UniqueConstraint("operation_id", name=op.f("uq_approval_decisions_operation_id")),
    )
    op.create_index(
        op.f("ix_approval_decisions_approval_ref"),
        "approval_decisions",
        ["approval_ref"],
        unique=False,
    )

    op.create_index(
        "ix_approval_decisions_resume_pending",
        "approval_decisions",
        ["resume_command_id", "deleted", "updated_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    """Decision operations are audit state; dropping them loses the trail."""
    if op.get_bind().execute(sa.text("SELECT 1 FROM approval_decisions LIMIT 1")).first() is not None:
        raise RuntimeError("Cannot downgrade: approval decision operations must be retained")
    op.drop_index("ix_approval_decisions_resume_pending", table_name="approval_decisions")
    op.drop_index(op.f("ix_approval_decisions_approval_ref"), table_name="approval_decisions")
    op.drop_table("approval_decisions")
