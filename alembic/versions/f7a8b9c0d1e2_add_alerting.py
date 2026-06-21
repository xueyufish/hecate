"""Add alerting tables.

Creates 5 tables for the alerting system:
- alert_rules: threshold-based alert rule definitions
- alert_events: alert instances with state machine (pending/firing/resolved/acked)
- alert_silences: maintenance windows that suppress notifications
- escalation_policies: reusable multi-step escalation rules
- notification_channels: configured notification targets (webhook/email/websocket)

Also seeds a default "Standard Escalation" policy.

Revision ID: f7a8b9c0d1e2
Revises: e66673e35d7c
Create Date: 2026-06-21 00:00:00
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "f7a8b9c0d1e2"
down_revision: str | None = "e66673e35d7c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_WORKSPACE = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("alert_type", sa.String(32), nullable=False),
        sa.Column("threshold", sa.Float, nullable=False),
        sa.Column("window_minutes", sa.Integer, nullable=False, server_default="5"),
        sa.Column("for_minutes", sa.Integer, nullable=False, server_default="2"),
        sa.Column("severity", sa.String(16), nullable=False, server_default="warning"),
        sa.Column("filters", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("escalation_policy_id", sa.String(36), nullable=True),
        sa.Column("channel_ids", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("workspace_id", sa.String(36), nullable=False, server_default=_DEFAULT_WORKSPACE),
    )
    op.create_index("ix_alert_rules_workspace_enabled", "alert_rules", ["workspace_id", "enabled"])
    op.create_index("ix_alert_rules_escalation_policy_id", "alert_rules", ["escalation_policy_id"])

    op.create_table(
        "alert_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rule_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("current_value", sa.Float, nullable=False, server_default="0"),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acked_by", sa.String(36), nullable=True),
        sa.Column("escalation_step", sa.Integer, nullable=False, server_default="0"),
        sa.Column("workspace_id", sa.String(36), nullable=False, server_default=_DEFAULT_WORKSPACE),
    )
    op.create_index("ix_alert_events_workspace_state", "alert_events", ["workspace_id", "state"])
    op.create_index("ix_alert_events_rule_id", "alert_events", ["rule_id"])

    op.create_table(
        "alert_silences",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("matchers", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("workspace_id", sa.String(36), nullable=False, server_default=_DEFAULT_WORKSPACE),
    )

    op.create_table(
        "escalation_policies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("steps", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("repeat_interval_min", sa.Integer, nullable=True),
        sa.Column("workspace_id", sa.String(36), nullable=False, server_default=_DEFAULT_WORKSPACE),
    )

    op.create_table(
        "notification_channels",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("channel_type", sa.String(32), nullable=False),
        sa.Column("config", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("workspace_id", sa.String(36), nullable=False, server_default=_DEFAULT_WORKSPACE),
    )

    bind = op.get_bind()
    existing = bind.execute(
        sa.text("SELECT COUNT(*) FROM escalation_policies WHERE name = :name"),
        {"name": "Standard Escalation"},
    ).scalar()
    if not existing:
        bind.execute(
            sa.text(
                "INSERT INTO escalation_policies "
                "(id, created_at, updated_at, deleted, name, steps, "
                "repeat_interval_min, workspace_id) "
                "VALUES (:id, :now, :now, 0, :name, :steps, :repeat, :ws)"
            ),
            {
                "id": str(uuid.uuid4()),
                "now": datetime.now(UTC),
                "name": "Standard Escalation",
                "steps": (
                    '[{"delay_min": 0, "channel_types": ["webhook_feishu", "websocket"]}, '
                    '{"delay_min": 15, "channel_types": ["email"]}]'
                ),
                "repeat": 60,
                "ws": _DEFAULT_WORKSPACE,
            },
        )


def downgrade() -> None:
    op.drop_table("notification_channels")
    op.drop_table("escalation_policies")
    op.drop_table("alert_silences")
    op.drop_table("alert_events")
    op.drop_index("ix_alert_events_rule_id", table_name="alert_events")
    op.drop_index("ix_alert_events_workspace_state", table_name="alert_events")
    op.drop_index("ix_alert_rules_escalation_policy_id", table_name="alert_rules")
    op.drop_index("ix_alert_rules_workspace_enabled", table_name="alert_rules")
    op.drop_table("alert_rules")
