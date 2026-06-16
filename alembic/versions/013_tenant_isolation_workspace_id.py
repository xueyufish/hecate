"""Add workspace_id to 14 unscoped tables for tenant data isolation.

Revision ID: 013_tenant_isolation
Revises: 012_org_rbac_api_keys
Create Date: 2026-06-09

Adds workspace_id column to:
- conversations, messages, sessions, documents, evidence
- checkpoints, budget_snapshots
- workflow_versions, workflow_runs
- prompt_versions
- evaluation_datasets, evaluation_items, evaluation_runs, evaluation_scores

Backfill strategy:
1. Add nullable workspace_id to all 14 tables
2. Set default zero UUID on parent tables
3. Inherit workspace_id from parent for child tables
4. Add NOT NULL constraint + composite indexes
5. Add FK to workspaces(id) for PostgreSQL only
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "013_tenant_isolation"
down_revision = "012b_eval_tables"
branch_labels = None
depends_on = None

_DEFAULT_UUID = "00000000-0000-0000-0000-000000000000"

# Parent tables (no parent entity — default to zero UUID)
_PARENT_TABLES = [
    "conversations",
    "sessions",
    "documents",
    "workflow_versions",
    "prompt_versions",
]

# Child tables with backfill mapping: (table, parent_table, fk_column)
_CHILD_TABLES: list[tuple[str, str, str]] = [
    ("messages", "conversations", "conversation_id"),
    ("evidences", "sessions", "session_id"),
    ("checkpoints", "sessions", "session_id"),
    ("budget_snapshots", "sessions", "session_id"),
    ("workflow_runs", "workflows", "workflow_id"),
]

# All 14 tables
_ALL_TABLES = _PARENT_TABLES + [t[0] for t in _CHILD_TABLES]

# Tables with a `deleted` column get composite (workspace_id, deleted) index
_TABLES_WITH_DELETED = [
    "conversations",
    "messages",
    "sessions",
    "documents",
    "evidences",
    "budget_snapshots",
    "workflow_versions",
    "workflow_runs",
    "prompt_versions",
]


def upgrade() -> None:
    # Step 1: Add nullable workspace_id to all 14 tables
    for table_name in _ALL_TABLES:
        op.add_column(
            table_name,
            sa.Column("workspace_id", sa.Uuid(), nullable=True),
        )

    # Step 2: Backfill parent tables with zero UUID
    for table_name in _PARENT_TABLES:
        op.execute(
            f"UPDATE {table_name} SET workspace_id = '{_DEFAULT_UUID}' WHERE workspace_id IS NULL"  # noqa: S608
        )

    # Step 3: Backfill child tables from parent (SQLite-compatible subquery)
    for child_table, parent_table, fk_col in _CHILD_TABLES:
        op.execute(
            f"UPDATE {child_table} SET workspace_id = "  # noqa: S608
            f"(SELECT workspace_id FROM {parent_table} WHERE {parent_table}.id = {child_table}.{fk_col}) "
            f"WHERE workspace_id IS NULL"
        )
        # Orphan rows (parent not found) get zero UUID fallback
        op.execute(
            f"UPDATE {child_table} SET workspace_id = '{_DEFAULT_UUID}' WHERE workspace_id IS NULL"  # noqa: S608
        )

    # Step 4: Make workspace_id NOT NULL
    for table_name in _ALL_TABLES:
        op.alter_column(
            table_name,
            "workspace_id",
            nullable=False,
            server_default=sa.text(f"'{_DEFAULT_UUID}'"),
        )

    # Step 5: Add composite indexes
    for table_name in _TABLES_WITH_DELETED:
        op.create_index(
            f"idx_{table_name}_workspace",
            table_name,
            ["workspace_id", "deleted"],
        )

    # checkpoints has no `deleted` column — simple index
    op.create_index("idx_checkpoints_workspace", "checkpoints", ["workspace_id"])

    # Step 6: Add FK constraints (PostgreSQL only — SQLite doesn't support ALTER ADD FK)
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect != "sqlite":
        for table_name in _ALL_TABLES:
            op.create_foreign_key(
                f"fk_{table_name}_workspace_id",
                table_name,
                "workspaces",
                ["workspace_id"],
                ["id"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Remove FK constraints (PostgreSQL only)
    if dialect != "sqlite":
        for table_name in _ALL_TABLES:
            op.drop_constraint(f"fk_{table_name}_workspace_id", table_name, type_="foreignkey")

    # Drop indexes
    for table_name in _TABLES_WITH_DELETED:
        op.drop_index(f"idx_{table_name}_workspace", table_name=table_name)
    op.drop_index("idx_checkpoints_workspace", table_name="checkpoints")

    # Drop columns
    for table_name in _ALL_TABLES:
        op.drop_column(table_name, "workspace_id")
