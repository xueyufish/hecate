"""add backup_records table

Revision ID: z4f5a6b7c8d9
Revises: y3e4f5a6b7c8
Create Date: 2026-07-30

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "z4f5a6b7c8d9"
down_revision = "y3e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create backup_records table."""
    op.create_table(
        "backup_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("backup_type", sa.String(20), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("storage_type", sa.String(20), nullable=False),
        sa.Column("storage_path", sa.String(1024), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("checksum", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_status", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backup_records_status", "backup_records", ["status"])
    op.create_index("ix_backup_records_scope_ts", "backup_records", ["scope", "started_at"])


def downgrade() -> None:
    """Drop backup_records table."""
    op.drop_index("ix_backup_records_scope_ts", table_name="backup_records")
    op.drop_index("ix_backup_records_status", table_name="backup_records")
    op.drop_table("backup_records")
