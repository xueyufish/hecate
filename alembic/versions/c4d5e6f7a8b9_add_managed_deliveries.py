"""add managed_deliveries for the managed runner channel (step6/7)

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-10-04

One row per platform dispatch intent toward an enrolled host; single
writer ManagedDeliveryService. The outbox property: the intent exists
before the host acts, and a lost response is reconciled by redelivery +
the host's idempotent accept, never by a second execution.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "c4d5e6f7a8b9"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "managed_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enrollment_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("task_ref", sa.JSON(), nullable=False),
        sa.Column("run_ref", sa.JSON(), nullable=False),
        sa.Column("input_payload", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("delivery_ref", sa.JSON(), nullable=True),
        sa.Column("accepted_refs", sa.JSON(), nullable=True),
        sa.Column("accepted_at", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["enrollment_id"], ["standalone_enrollments.id"], name="fk_managed_deliveries_enrollment"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_managed_deliveries_workspace"),
    )
    op.create_index(
        "ix_managed_deliveries_enrollment_state", "managed_deliveries", ["enrollment_id", "state"]
    )
    op.create_index("ix_managed_deliveries_enrollment_id", "managed_deliveries", ["enrollment_id"])
    op.create_index("ix_managed_deliveries_workspace_id", "managed_deliveries", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_managed_deliveries_workspace_id", table_name="managed_deliveries")
    op.drop_index("ix_managed_deliveries_enrollment_id", table_name="managed_deliveries")
    op.drop_index("ix_managed_deliveries_enrollment_state", table_name="managed_deliveries")
    op.drop_table("managed_deliveries")
