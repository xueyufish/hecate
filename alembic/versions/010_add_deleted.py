"""Add deleted boolean field to all BaseModel tables and replace partial indexes.

Revision ID: 010_add_deleted
Revises: 009_skill_workspace
Create Date: 2026-06-07
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "010_add_deleted"
down_revision = "009_skill_workspace"
branch_labels = None
depends_on = None

# Tables that inherit BaseModel and need the deleted column
BASEMODEL_TABLES = [
    "agents",
    "conversations",
    "sessions",
    "messages",
    "knowledge_bases",
    "documents",
    "tools",
    "skills",
    "model_providers",
    "model_registry",
    "workflows",
    "workflow_versions",
    "workflow_runs",
    "memory_blocks",
    "memories",
    "prompts",
    "prompt_versions",
    "evidence",
    "budget_snapshots",
]

# Partial indexes to drop (postgresql_where= deleted_at IS NULL)
PARTIAL_INDEXES = [
    ("idx_agents_workspace", "agents"),
    ("idx_conversations_agent", "conversations"),
    ("idx_documents_kb", "documents"),
    ("idx_tools_workspace_name", "tools"),
    ("idx_skills_workspace_name", "skills"),
    ("idx_model_providers_name", "model_providers"),
    ("uq_model_registry_provider_model", "model_registry"),
    ("idx_model_registry_provider", "model_registry"),
    ("idx_model_registry_model_id", "model_registry"),
    ("idx_workflows_workspace", "workflows"),
]


def upgrade() -> None:
    """Add deleted column, backfill from deleted_at, replace indexes."""

    # Step 1: Add deleted boolean column with server_default=False
    for table in BASEMODEL_TABLES:
        op.add_column(
            table,
            sa.Column("deleted", sa.Boolean(), nullable=False, server_default="0"),
        )

    # Step 2: Backfill deleted from deleted_at
    # NULL deleted_at → deleted=False (0), non-NULL deleted_at → deleted=True (1)
    for table in BASEMODEL_TABLES:
        op.execute(f"UPDATE {table} SET deleted = 1 WHERE deleted_at IS NOT NULL")  # noqa: S608

    # Step 3: Drop old partial indexes
    for idx_name, table_name in PARTIAL_INDEXES:
        op.drop_index(idx_name, table_name=table_name)

    # Step 4: Create new composite indexes
    op.create_index("idx_agents_workspace", "agents", ["workspace_id", "deleted"])
    op.create_index("idx_conversations_agent", "conversations", ["agent_id", "deleted"])
    op.create_index("idx_documents_kb", "documents", ["knowledge_base_id", "deleted"])
    op.create_index(
        "idx_tools_workspace_name",
        "tools",
        ["workspace_id", "name", "deleted", "deleted_at"],
        unique=True,
    )
    op.create_index(
        "idx_skills_workspace_name",
        "skills",
        ["workspace_id", "name", "deleted", "deleted_at"],
        unique=True,
    )
    op.create_index("idx_model_providers_name", "model_providers", ["name", "deleted"])
    op.create_index(
        "uq_model_registry_provider_model",
        "model_registry",
        ["provider_id", "model_id", "deleted", "deleted_at"],
        unique=True,
    )
    op.create_index("idx_model_registry_provider", "model_registry", ["provider_id", "deleted"])
    op.create_index("idx_model_registry_model_id", "model_registry", ["model_id", "deleted"])
    op.create_index("idx_workflows_workspace", "workflows", ["workspace_id", "deleted"])


def downgrade() -> None:
    """Remove new indexes, drop deleted column."""

    # Drop new composite indexes
    op.drop_index("idx_workflows_workspace", table_name="workflows")
    op.drop_index("idx_model_registry_model_id", table_name="model_registry")
    op.drop_index("idx_model_registry_provider", table_name="model_registry")
    op.drop_index("uq_model_registry_provider_model", table_name="model_registry")
    op.drop_index("idx_model_providers_name", table_name="model_providers")
    op.drop_index("idx_skills_workspace_name", table_name="skills")
    op.drop_index("idx_tools_workspace_name", table_name="tools")
    op.drop_index("idx_documents_kb", table_name="documents")
    op.drop_index("idx_conversations_agent", table_name="conversations")
    op.drop_index("idx_agents_workspace", table_name="agents")

    # Recreate old partial indexes (PostgreSQL-only)
    op.create_index(
        "idx_workflows_workspace", "workflows", ["workspace_id"], postgresql_where=sa.text("deleted_at IS NULL")
    )
    op.create_index(
        "idx_model_registry_model_id", "model_registry", ["model_id"], postgresql_where=sa.text("deleted_at IS NULL")
    )
    op.create_index(
        "idx_model_registry_provider", "model_registry", ["provider_id"], postgresql_where=sa.text("deleted_at IS NULL")
    )
    op.create_index(
        "uq_model_registry_provider_model",
        "model_registry",
        ["provider_id", "model_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_model_providers_name", "model_providers", ["name"], postgresql_where=sa.text("deleted_at IS NULL")
    )
    op.create_index(
        "idx_skills_workspace_name",
        "skills",
        ["workspace_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_tools_workspace_name",
        "tools",
        ["workspace_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_documents_kb", "documents", ["knowledge_base_id"], postgresql_where=sa.text("deleted_at IS NULL")
    )
    op.create_index(
        "idx_conversations_agent", "conversations", ["agent_id"], postgresql_where=sa.text("deleted_at IS NULL")
    )
    op.create_index("idx_agents_workspace", "agents", ["workspace_id"], postgresql_where=sa.text("deleted_at IS NULL"))

    # Drop deleted column
    for table in reversed(BASEMODEL_TABLES):
        op.drop_column(table, "deleted")
