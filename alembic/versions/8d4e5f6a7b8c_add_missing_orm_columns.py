"""Add columns present in ORM models but missing from migrations.

Closes the schema/ORM drift found by the fresh-database drift gate
(tests/test_migrations/test_upgrade_drift.py): backup_records /
budget_snapshots / security_findings were missing BaseModel timestamp
and soft-delete columns, memories / memory_blocks were missing the
workspace_id tenant-isolation column, and tools was missing the
caching configuration columns (feature 5.7 Tool Caching).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "8d4e5f6a7b8c"
down_revision = "7c3d4e5f6a7b"
branch_labels = None
depends_on = None

_ZERO_UUID = sa.text("'00000000-0000-0000-0000-000000000000'")


def upgrade() -> None:
    op.add_column("backup_records", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("budget_snapshots", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "security_findings",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.add_column("security_findings", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("memories", sa.Column("workspace_id", sa.Uuid(), nullable=False, server_default=_ZERO_UUID))
    op.add_column("memory_blocks", sa.Column("workspace_id", sa.Uuid(), nullable=False, server_default=_ZERO_UUID))
    op.add_column("tools", sa.Column("cacheable", sa.Boolean(), nullable=True))
    op.add_column("tools", sa.Column("cache_ttl", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("tools", "cache_ttl")
    op.drop_column("tools", "cacheable")
    op.drop_column("memory_blocks", "workspace_id")
    op.drop_column("memories", "workspace_id")
    op.drop_column("security_findings", "deleted_at")
    op.drop_column("security_findings", "updated_at")
    op.drop_column("budget_snapshots", "deleted_at")
    op.drop_column("backup_records", "deleted_at")
