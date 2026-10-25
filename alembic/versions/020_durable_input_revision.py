"""Add the durable input-revision table for controller-loop input history.

Revision ID: 020_durable_input_revision
Revises: 019_agent_publication_receipt

Mirrors ``hecate_durable.storage.models.InputRevisionRow`` (the package
creates its own tables from metadata on standalone hosts; this migration is
the platform-managed deployment path). Expand-only: no existing table is
touched. One immutable accepted input revision per task, unique per
(task, source command) for idempotent wake replays; ``effective_turn`` is
the causal boundary — one past the committed decision-chain length at
acceptance — and the content digest detects corruption on read.
"""

import sqlalchemy as sa
from alembic import op

revision = "020_durable_input_revision"
down_revision = "019_agent_publication_receipt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add input-revision storage without altering existing tables."""
    op.create_table(
        "durable_input_revision",
        sa.Column("revision_id", sa.String(length=64), nullable=False),
        sa.Column("task_issuer", sa.String(length=256), nullable=False),
        sa.Column("task_id", sa.String(length=256), nullable=False),
        sa.Column("run_issuer", sa.String(length=256), nullable=False),
        sa.Column("run_id", sa.String(length=256), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.String(length=256), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("content_sha256", sa.String(length=128), nullable=False),
        sa.Column("wait_token", sa.String(length=256), nullable=True),
        sa.Column("effective_turn", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("revision_id", name=op.f("pk_durable_input_revision")),
        sa.UniqueConstraint(
            "task_issuer",
            "task_id",
            "source_ref",
            name=op.f("uq_durable_input_revision_task_issuer"),
        ),
    )


def downgrade() -> None:
    """Input history is authoritative state; dropping it loses recovery facts."""
    if op.get_bind().execute(sa.text("SELECT 1 FROM durable_input_revision LIMIT 1")).first() is not None:
        raise RuntimeError("Cannot downgrade: durable input history must be retained")
    op.drop_table("durable_input_revision")
