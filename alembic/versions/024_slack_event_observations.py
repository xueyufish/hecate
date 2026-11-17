"""Observation ledger for the Slack event-observation entry point.

Revision ID: 024_slack_event_observations
Revises: 023_task_live_input_delivery

Observed deliveries use a workspace-scoped unique key. Signed refusal
counters occupy a separate internal key space and carry no payload fields.
"""

import sqlalchemy as sa
from alembic import op

revision = "024_slack_event_observations"
down_revision = "023_task_live_input_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the observation ledger with its dedup and retention indexes."""
    op.create_table(
        "slack_event_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("app_id", sa.String(length=64), nullable=False),
        sa.Column("team_id", sa.String(length=64), nullable=False),
        sa.Column("channel_id", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("disposition", sa.String(length=64), nullable=False),
        sa.Column("delivery_count", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("workspace_id", "source", "event_id", name="uq_slack_event_observations_workspace_event"),
    )
    op.create_index(
        "ix_slack_event_observations_created_at",
        "slack_event_observations",
        ["created_at"],
    )


def downgrade() -> None:
    """Remove an empty ledger without discarding retained observations."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("LOCK TABLE slack_event_observations IN ACCESS EXCLUSIVE MODE")
    if bind.execute(sa.text("SELECT 1 FROM slack_event_observations LIMIT 1")).first() is not None:
        raise RuntimeError("Cannot downgrade: Slack observation evidence must be retained")
    op.drop_index("ix_slack_event_observations_created_at", table_name="slack_event_observations")
    op.drop_table("slack_event_observations")
