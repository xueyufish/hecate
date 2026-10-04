"""add platform task control tables (step6 platform track)

Revision ID: f7d8e9a0b1c2
Revises: e9a4b72c6d10
Create Date: 2026-10-04

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "f7d8e9a0b1c2"
down_revision = "e9a4b72c6d10"
branch_labels = None
depends_on = None

_BASE_COLUMNS = (
    sa.Column("id", sa.Uuid(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
    sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
)


def upgrade() -> None:
    """Create step6 platform tables: lifecycle, submissions, commands, events."""
    op.create_table(
        "task_lifecycle_states",
        *_BASE_COLUMNS,
        sa.Column("issuer_domain", sa.String(255), nullable=False),
        sa.Column("task_ref_id", sa.String(512), nullable=False),
        sa.Column("lifecycle_state", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.Text(), nullable=False),
        sa.Column("writer_source", sa.String(255), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_task_lifecycle_ref",
        "task_lifecycle_states",
        ["issuer_domain", "task_ref_id"],
        unique=True,
    )
    op.create_index(
        "ix_task_lifecycle_workspace_state",
        "task_lifecycle_states",
        ["workspace_id", "lifecycle_state"],
    )

    op.create_table(
        "task_submissions",
        *_BASE_COLUMNS,
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("workspace", sa.String(64), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("task_issuer_domain", sa.String(255), nullable=False),
        sa.Column("task_ref_id", sa.String(512), nullable=False),
        sa.Column("run_issuer_domain", sa.String(255), nullable=False),
        sa.Column("run_ref_id", sa.String(512), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_task_submissions_key", "task_submissions", ["idempotency_key"], unique=True)
    op.create_index(
        "ix_task_submissions_task_ref",
        "task_submissions",
        ["task_issuer_domain", "task_ref_id"],
    )

    op.create_table(
        "control_commands",
        *_BASE_COLUMNS,
        sa.Column("command_id", sa.String(512), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("issuer", sa.String(255), nullable=False),
        sa.Column("task_issuer_domain", sa.String(255), nullable=False),
        sa.Column("task_ref_id", sa.String(512), nullable=False),
        sa.Column("run_issuer_domain", sa.String(255), nullable=True),
        sa.Column("run_ref_id", sa.String(512), nullable=True),
        sa.Column("issued_at", sa.Text(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=True),
        sa.Column("expected_revision", sa.Integer(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_schema_ref", sa.Text(), nullable=True),
        sa.Column("detail_ns", sa.JSON(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_control_commands_command_id", "control_commands", ["command_id"], unique=True)
    op.create_index(
        "ix_control_commands_task_ref",
        "control_commands",
        ["task_issuer_domain", "task_ref_id"],
    )
    op.create_index(
        "ix_control_commands_workspace_state",
        "control_commands",
        ["workspace_id", "state"],
    )

    op.create_table(
        "platform_events",
        *_BASE_COLUMNS,
        sa.Column("event_id", sa.String(512), nullable=False),
        sa.Column("contract_version", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("task_issuer_domain", sa.String(255), nullable=False),
        sa.Column("task_ref_id", sa.String(512), nullable=False),
        sa.Column("run_issuer_domain", sa.String(255), nullable=False),
        sa.Column("run_ref_id", sa.String(512), nullable=False),
        sa.Column("source_sequence", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.Text(), nullable=False),
        sa.Column("received_at", sa.Text(), nullable=False),
        sa.Column("payload_schema_ref", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("correlation_id", sa.String(512), nullable=True),
        sa.Column("causation_id", sa.String(512), nullable=True),
        sa.Column("actor_kind", sa.String(32), nullable=True),
        sa.Column("actor_id", sa.String(255), nullable=True),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column("envelope", sa.JSON(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_platform_events_event_id", "platform_events", ["event_id"], unique=True)
    op.create_index(
        "ix_platform_events_run_sequence",
        "platform_events",
        ["workspace_id", "run_issuer_domain", "run_ref_id", "source_sequence"],
    )
    op.create_index(
        "ix_platform_events_task_ref",
        "platform_events",
        ["task_issuer_domain", "task_ref_id"],
    )


def downgrade() -> None:
    """Drop the step6 platform tables (no historical data to preserve)."""
    op.drop_index("ix_platform_events_task_ref", table_name="platform_events")
    op.drop_index("ix_platform_events_run_sequence", table_name="platform_events")
    op.drop_index("uq_platform_events_event_id", table_name="platform_events")
    op.drop_table("platform_events")

    op.drop_index("ix_control_commands_workspace_state", table_name="control_commands")
    op.drop_index("ix_control_commands_task_ref", table_name="control_commands")
    op.drop_index("uq_control_commands_command_id", table_name="control_commands")
    op.drop_table("control_commands")

    op.drop_index("ix_task_submissions_task_ref", table_name="task_submissions")
    op.drop_index("uq_task_submissions_key", table_name="task_submissions")
    op.drop_table("task_submissions")

    op.drop_index("ix_task_lifecycle_workspace_state", table_name="task_lifecycle_states")
    op.drop_index("uq_task_lifecycle_ref", table_name="task_lifecycle_states")
    op.drop_table("task_lifecycle_states")
