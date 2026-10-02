"""add tasks, runs, standalone enrollments and conversation links

Revision ID: c1d2e3f4a5b6
Revises: 8e4f2a6c9d17
Create Date: 2026-10-01

Step4 second half (change task-run-model): create the four new tables with
their full constraint set. All tables start empty - constraints are complete
from the start (the plan's expand->migrate->contract relaxation applies to
alters of populated tables, not fresh ones). No existing table or hot path
is touched; legacy chat sessions get conversation links lazily via the
registry service, so no data backfill revision is needed.

Application rollback retains these tables and their records. Schema downgrade
is refused even before the later execution flows are deployed: registration
services can already create governance and observation records.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "8e4f2a6c9d17"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("goal", sa.String(length=4096), nullable=False),
        sa.Column("initiator_ref", sa.JSON(), nullable=False),
        sa.Column("acceptance", sa.JSON(), nullable=False),
        sa.Column("responsibility", sa.String(length=512), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("issuer_domain", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tasks_workspace_id"), "tasks", ["workspace_id"], unique=False)

    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("deployment_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("issuer_domain", sa.String(length=255), nullable=False),
        sa.Column("identity_chain", sa.JSON(), nullable=False),
        sa.Column("backend_ref", sa.JSON(), nullable=False),
        sa.Column("backend_session", sa.JSON(), nullable=True),
        sa.Column("event_cursor", sa.Integer(), nullable=False),
        sa.Column("projection", sa.JSON(), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("local_source", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["deployment_id"], ["agent_deployments.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    # One attempt slot per (task, attempt): retries are new rows, and a
    # concurrent double-dispatch cannot share a counter.
    op.create_index("uq_runs_task_attempt", "runs", ["task_id", "attempt_no"], unique=True)
    # One vendor session can never silently back two runs. PostgreSQL cannot
    # index a json column with btree, so the partial unique index is on the
    # canonical text form; SQLite (tests) indexes the column directly - the
    # semantics (non-null session binds to at most one run) are the same.
    op.create_index(
        "uq_runs_backend_session",
        "runs",
        [sa.text("((backend_session::text))")],
        unique=True,
        postgresql_where=sa.text("backend_session IS NOT NULL"),
        sqlite_where=sa.text("backend_session IS NOT NULL"),
    )
    op.create_index(op.f("ix_runs_task_id"), "runs", ["task_id"], unique=False)
    op.create_index(op.f("ix_runs_workspace_id"), "runs", ["workspace_id"], unique=False)

    op.create_table(
        "standalone_enrollments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("host_identity_ref", sa.JSON(), nullable=False),
        sa.Column("trust_root_ref", sa.JSON(), nullable=False),
        sa.Column("installed_versions", sa.JSON(), nullable=False),
        sa.Column("managed_new_runs", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("operator_id", sa.Uuid(), nullable=True),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "admission",
            sa.Enum("PENDING", "ADMITTED", "REJECTED", name="enrollment_admission_result"),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_standalone_enrollments_workspace_id"),
        "standalone_enrollments",
        ["workspace_id"],
        unique=False,
    )

    op.create_table(
        "conversation_task_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("governance_status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", "task_id", name="uq_conversation_task"),
        # One task belongs to exactly one originating conversation; a
        # conversation may link many tasks.
        sa.UniqueConstraint("task_id", name="uq_conversation_task_task"),
    )
    op.create_index(
        op.f("ix_conversation_task_links_conversation_id"),
        "conversation_task_links",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_conversation_task_links_workspace_id"),
        "conversation_task_links",
        ["workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    raise RuntimeError("Step4 records must be retained; roll back the application without downgrading schema")
