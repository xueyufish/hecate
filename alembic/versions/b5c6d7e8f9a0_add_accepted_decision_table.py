"""Add the durable accepted-decision table for agent decisions.

Revision ID: b5c6d7e8f9a0
Revises: 7a6e5d4c3b20

Mirrors ``hecate_durable.storage.models.AcceptedDecisionRow`` (the package
creates its own tables from metadata on standalone hosts; this migration is
the platform-managed deployment path). Expand-only: no existing table is
touched. One accepted Agent decision per (issuer_domain, run_id,
decision_step_id); the content digest arbitrates idempotent re-submission
versus explicit conflict; fence columns carry the submitter's fencing context.
"""

import sqlalchemy as sa
from alembic import op

revision = "b5c6d7e8f9a0"
down_revision = "7a6e5d4c3b20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add decision storage without altering existing tables."""
    op.create_table(
        "durable_accepted_decision",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("issuer_domain", sa.String(length=256), nullable=False),
        sa.Column("run_id", sa.String(length=256), nullable=False),
        sa.Column("decision_step_id", sa.String(length=256), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("input_refs", sa.JSON(), nullable=False),
        sa.Column("accepted_output", sa.JSON(), nullable=False),
        sa.Column("actions", sa.JSON(), nullable=False),
        sa.Column("content_digest", sa.String(length=128), nullable=False),
        sa.Column("fence_holder", sa.String(length=256), nullable=True),
        sa.Column("fence_token", sa.Integer(), nullable=False),
        sa.Column("accepted_at", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_durable_accepted_decision")),
        sa.UniqueConstraint(
            "issuer_domain",
            "run_id",
            "decision_step_id",
            name=op.f("uq_durable_accepted_decision_issuer_domain"),
        ),
    )


def downgrade() -> None:
    """Preserve accepted decisions across application rollback."""
    raise NotImplementedError("Accepted decisions are durable facts; retain this expand-only schema on rollback")
