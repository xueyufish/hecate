"""add durable worker tables, retire track-B control tables (step6 worker)

Revision ID: a2b3c4d5e6f7
Revises: f7d8e9a0b1c2
Create Date: 2026-10-04

Creates the hecate-durable package tables (task state, submissions,
commands, action ledger, event-log outbox, run sequences, leases, relay
cursor) as the single authoritative home for durable execution state, and
drops the track-B parallel tables (``task_lifecycle_states``,
``task_submissions``, ``control_commands``).

Data migration: NONE — both tracks landed on the same day on an alpha
deployment with no production data; the retired tables' contents are not
preserved. The authoritative ``platform_events`` read model survives.

Downgrade: recreates the three retired tables EMPTY (structures copied from
the track-B migration); the durable tables are kept so execution facts are
never destroyed. Rolling the application back across this migration
therefore loses lifecycle/command rows written under the worker binding —
drain before downgrade.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "a2b3c4d5e6f7"
down_revision = "f7d8e9a0b1c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create hecate-durable tables; drop the retired track-B tables."""
    op.create_table(
        "durable_task_state",
        sa.Column("task_issuer", sa.String(256), nullable=False),
        sa.Column("task_id", sa.String(256), nullable=False),
        sa.Column("lifecycle_state", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.String(64), nullable=False),
        sa.Column("writer_source", sa.String(64), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("run_issuer", sa.String(256), nullable=True),
        sa.Column("run_id", sa.String(256), nullable=True),
        sa.Column("input_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("task_issuer", "task_id"),
    )
    op.create_table(
        "durable_submission",
        sa.Column("key", sa.String(256), nullable=False),
        sa.Column("subject", sa.String(256), nullable=False),
        sa.Column("workspace", sa.String(256), nullable=False),
        sa.Column("request_digest", sa.String(128), nullable=False),
        sa.Column("task_issuer", sa.String(256), nullable=False),
        sa.Column("task_id", sa.String(256), nullable=False),
        sa.Column("run_issuer", sa.String(256), nullable=False),
        sa.Column("run_id", sa.String(256), nullable=False),
        sa.Column("created_at", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "durable_command",
        sa.Column("command_id", sa.String(256), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("issuer", sa.String(256), nullable=False),
        sa.Column("task_issuer", sa.String(256), nullable=False),
        sa.Column("task_id", sa.String(256), nullable=False),
        sa.Column("issued_at", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("run_issuer", sa.String(256), nullable=True),
        sa.Column("run_id", sa.String(256), nullable=True),
        sa.Column("expires_at", sa.String(64), nullable=True),
        sa.Column("expected_revision", sa.Integer(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_schema_ref", sa.String(256), nullable=True),
        sa.Column("detail_ns", sa.JSON(), nullable=False),
        sa.Column("extra", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("command_id"),
    )
    op.create_table(
        "durable_action_intent",
        sa.Column("action_key", sa.String(512), nullable=False),
        sa.Column("action_name", sa.String(256), nullable=False),
        sa.Column("arguments_digest", sa.String(128), nullable=False),
        sa.Column("side_effect_class", sa.String(32), nullable=False),
        sa.Column("task_issuer", sa.String(256), nullable=True),
        sa.Column("task_id", sa.String(256), nullable=True),
        sa.Column("run_issuer", sa.String(256), nullable=True),
        sa.Column("run_id", sa.String(256), nullable=True),
        sa.Column("session_id", sa.String(256), nullable=True),
        sa.Column("execution_id", sa.String(256), nullable=True),
        sa.Column("tool_call_id", sa.String(256), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("claim_token", sa.Integer(), nullable=False),
        sa.Column("claim_holder", sa.String(256), nullable=True),
        sa.Column("claimed_at", sa.String(64), nullable=True),
        sa.Column("created_at", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("action_key"),
    )
    op.create_table(
        "durable_action_outcome",
        sa.Column("action_key", sa.String(512), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("result_digest", sa.String(128), nullable=True),
        sa.Column("result_ref", sa.JSON(), nullable=True),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column("recorded_at", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("action_key"),
    )
    op.create_table(
        "durable_event_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_issuer", sa.String(256), nullable=False),
        sa.Column("run_id", sa.String(256), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_sequence", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("envelope", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_issuer", "run_id", "source", "source_sequence", name="uq_event_stream_position"),
        sa.UniqueConstraint("event_id", name="uq_durable_event_log_event_id"),
    )
    op.create_table(
        "durable_run_sequence",
        sa.Column("run_issuer", sa.String(256), nullable=False),
        sa.Column("run_id", sa.String(256), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("run_issuer", "run_id", "source"),
    )
    op.create_table(
        "durable_lease",
        sa.Column("lease_key", sa.String(256), nullable=False),
        sa.Column("holder", sa.String(256), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.String(64), nullable=False),
        sa.Column("expires_at_epoch", sa.Float(), nullable=False),
        sa.Column("acquired_at", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("lease_key"),
    )
    op.create_table(
        "durable_outbox_cursor",
        sa.Column("relay_key", sa.String(128), nullable=False),
        sa.Column("last_event_row_id", sa.Integer(), nullable=False),
        sa.Column("skipped_event_ids", sa.JSON(), nullable=False),
        sa.Column("failing_event_id", sa.String(64), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("relay_key"),
    )

    op.drop_table("control_commands")
    op.drop_table("task_submissions")
    op.drop_table("task_lifecycle_states")


def downgrade() -> None:
    """Recreate the retired track-B tables EMPTY; durable tables survive."""

    op.create_table(
        "task_lifecycle_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issuer_domain", sa.String(255), nullable=False),
        sa.Column("task_ref_id", sa.String(512), nullable=False),
        sa.Column("lifecycle_state", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.Text(), nullable=False),
        sa.Column("writer_source", sa.String(255), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_task_lifecycle_ref", "task_lifecycle_states", ["issuer_domain", "task_ref_id"], unique=True)
    op.create_index("ix_task_lifecycle_workspace_state", "task_lifecycle_states", ["workspace_id", "lifecycle_state"])
    op.create_table(
        "task_submissions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("workspace", sa.String(255), nullable=False),
        sa.Column("request_digest", sa.String(128), nullable=False),
        sa.Column("task_issuer", sa.String(255), nullable=False),
        sa.Column("task_ref_id", sa.String(512), nullable=False),
        sa.Column("run_issuer", sa.String(255), nullable=False),
        sa.Column("run_ref_id", sa.String(512), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_task_submission_key", "task_submissions", ["idempotency_key"], unique=True)
    op.create_table(
        "control_commands",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("command_id", sa.String(512), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("issuer", sa.String(255), nullable=False),
        sa.Column("task_issuer", sa.String(255), nullable=False),
        sa.Column("task_ref_id", sa.String(512), nullable=False),
        sa.Column("issued_at", sa.Text(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("run_issuer", sa.String(255), nullable=True),
        sa.Column("run_ref_id", sa.String(512), nullable=True),
        sa.Column("expires_at", sa.Text(), nullable=True),
        sa.Column("expected_revision", sa.Integer(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_schema_ref", sa.String(255), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_control_command_id", "control_commands", ["command_id"], unique=True)
