"""Fix uuid-typed columns stored as varchar(36).

Several tables created by early migrations kept their UUID columns as
``character varying(36)`` while the ORM models later moved to native
``sa.Uuid``. PostgreSQL rejects ``varchar = uuid`` comparisons, so any
endpoint filtering those tables by workspace (alerts, quotas, model
pricing, approvals, tool policies, ...) fails with
``UndefinedFunctionError: operator does not exist: character varying =
uuid``. SQLite-based unit tests never exercise the dialect mismatch,
so this only surfaced in a live-PG end-to-end pass.

Converts every affected column to native ``uuid`` with a
``USING col::uuid`` cast (the zero-UUID default value casts cleanly).

Revision ID: e8f9a0b1c2d3
Revises: c5d6e7f8a9b0
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8f9a0b1c2d3"
down_revision: str | None = "c5d6e7f8a9b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column) pairs where the ORM declares a UUID but the column is
# still varchar(36). Derived from an ORM-metadata vs information_schema
# diff on a migrated database; keep in sync when models change.
_COLUMNS: list[tuple[str, str]] = [
    ("alert_events", "id"),
    ("alert_events", "rule_id"),
    ("alert_events", "workspace_id"),
    ("alert_events", "acked_by"),
    ("alert_rules", "id"),
    ("alert_rules", "workspace_id"),
    ("alert_rules", "escalation_policy_id"),
    ("alert_silences", "id"),
    ("alert_silences", "workspace_id"),
    ("alert_silences", "created_by"),
    ("approval_records", "id"),
    ("approval_records", "session_id"),
    ("approval_records", "user_id"),
    ("approval_records", "workspace_id"),
    ("escalation_policies", "id"),
    ("escalation_policies", "workspace_id"),
    ("model_pricings", "id"),
    ("model_pricings", "workspace_id"),
    ("notification_channels", "id"),
    ("notification_channels", "workspace_id"),
    ("quota_usage", "id"),
    ("quota_usage", "quota_id"),
    ("quota_usage", "workspace_id"),
    ("quotas", "id"),
    ("quotas", "scope_id"),
    ("quotas", "workspace_id"),
    ("tool_policies", "id"),
    ("tool_policies", "workspace_id"),
    ("workflow_runs", "run_id"),
]


# Columns carrying a varchar zero-UUID server default — the default must
# be dropped before the type change (Postgres cannot cast it implicitly)
# and re-created as a uuid default afterwards.
_ZERO_UUID = "'00000000-0000-0000-0000-000000000000'::uuid"
_WITH_VARCHAR_DEFAULT: list[tuple[str, str]] = [
    (t, "workspace_id")
    for t in (
        "alert_events",
        "alert_rules",
        "alert_silences",
        "escalation_policies",
        "notification_channels",
        "quota_usage",
        "quotas",
    )
]


def upgrade() -> None:
    for table, column in _WITH_VARCHAR_DEFAULT:
        op.alter_column(table, column, server_default=None)
    for table, column in _COLUMNS:
        op.alter_column(
            table,
            column,
            type_=sa.UUID(),
            postgresql_using=f"{column}::uuid",
            existing_nullable=True,
        )
    for table, column in _WITH_VARCHAR_DEFAULT:
        op.alter_column(table, column, server_default=sa.text(_ZERO_UUID))


def downgrade() -> None:
    for table, column in _WITH_VARCHAR_DEFAULT:
        op.alter_column(
            table,
            column,
            server_default=sa.text("'00000000-0000-0000-0000-000000000000'::character varying"),
        )
    for table, column in _COLUMNS:
        op.alter_column(
            table,
            column,
            type_=sa.String(36),
            postgresql_using=f"{column}::text",
            existing_nullable=True,
        )
    for table, column in _WITH_VARCHAR_DEFAULT:
        op.alter_column(table, column, server_default=None)
