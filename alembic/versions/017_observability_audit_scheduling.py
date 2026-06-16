"""Create audit_logs, metrics, traces, scheduled_tasks, and scheduled_task_executions tables.

These tables are defined in ORM models but were missing CREATE TABLE migrations.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "017_observability"
down_revision = "a7e86a31f6fc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # audit_logs
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(50), nullable=True),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column("request_method", sa.String(10), nullable=True),
        sa.Column("request_path", sa.String(500), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("ip_address", sa.String(255), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_audit_logs_org_created", "audit_logs", ["org_id", "created_at"])
    op.create_index("idx_audit_logs_workspace_action", "audit_logs", ["workspace_id", "action", "created_at"])
    op.create_index("idx_audit_logs_user_created", "audit_logs", ["user_id", "created_at"])

    # metrics
    op.create_table(
        "metrics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("type", sa.String(32), nullable=False, server_default="counter"),
        sa.Column("tags", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("timestamp", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_metrics_name", "metrics", ["name"])
    op.create_index("ix_metrics_timestamp", "metrics", ["timestamp"])

    # traces
    op.create_table(
        "traces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("input_data", sa.JSON(), nullable=True),
        sa.Column("output_data", sa.JSON(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("level", sa.String(16), nullable=False, server_default="DEFAULT"),
        sa.Column("status", sa.String(16), nullable=False, server_default="started"),
        sa.Column("start_time", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("end_time", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_traces_trace_id", "traces", ["trace_id"])
    op.create_index("ix_traces_parent_id", "traces", ["parent_id"])
    op.create_index("ix_traces_session_id", "traces", ["session_id"])
    op.create_index("ix_traces_agent_id", "traces", ["agent_id"])

    # scheduled_tasks
    op.create_table(
        "scheduled_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("cron_expression", sa.String(100), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("workflow_id", sa.Uuid(), nullable=True),
        sa.Column("execution_config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("state", sa.String(20), nullable=False, server_default="active"),
        sa.Column("max_concurrent_runs", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("catch_up", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("timezone", sa.String(50), nullable=False, server_default="UTC"),
        sa.Column("next_run_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_scheduled_tasks_org_state", "scheduled_tasks", ["org_id", "state"])
    op.create_index("idx_scheduled_tasks_workspace_state", "scheduled_tasks", ["workspace_id", "state"])
    op.create_index("idx_scheduled_tasks_next_run", "scheduled_tasks", ["next_run_at"])

    # scheduled_task_executions
    op.create_table(
        "scheduled_task_executions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="success"),
        sa.Column("result_summary", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("triggered_by", sa.String(20), nullable=False, server_default="cron"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_task_executions_task_created", "scheduled_task_executions", ["task_id", "created_at"])
    op.create_index("idx_task_executions_task_status", "scheduled_task_executions", ["task_id", "status"])
    op.create_index("ix_scheduled_task_executions_task_id", "scheduled_task_executions", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_scheduled_task_executions_task_id", table_name="scheduled_task_executions")
    op.drop_index("idx_task_executions_task_status", table_name="scheduled_task_executions")
    op.drop_index("idx_task_executions_task_created", table_name="scheduled_task_executions")
    op.drop_table("scheduled_task_executions")

    op.drop_index("idx_scheduled_tasks_next_run", table_name="scheduled_tasks")
    op.drop_index("idx_scheduled_tasks_workspace_state", table_name="scheduled_tasks")
    op.drop_index("idx_scheduled_tasks_org_state", table_name="scheduled_tasks")
    op.drop_table("scheduled_tasks")

    op.drop_index("ix_traces_agent_id", table_name="traces")
    op.drop_index("ix_traces_session_id", table_name="traces")
    op.drop_index("ix_traces_parent_id", table_name="traces")
    op.drop_index("ix_traces_trace_id", table_name="traces")
    op.drop_table("traces")

    op.drop_index("ix_metrics_timestamp", table_name="metrics")
    op.drop_index("ix_metrics_name", table_name="metrics")
    op.drop_table("metrics")

    op.drop_index("idx_audit_logs_user_created", table_name="audit_logs")
    op.drop_index("idx_audit_logs_workspace_action", table_name="audit_logs")
    op.drop_index("idx_audit_logs_org_created", table_name="audit_logs")
    op.drop_table("audit_logs")
