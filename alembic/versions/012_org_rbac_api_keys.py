"""Add organizations, workspaces, RBAC, and API key tables.

Revision ID: 012_org_rbac_api_keys
Revises: 011_rename_qdrant_collection
Create Date: 2026-06-08

Creates:
- organizations table
- workspaces table
- workspace_members table (with workspace_role enum)
- api_keys table (with api_key_scope enum)
- Bootstrap default org and default workspace (zero-UUID IDs)
- FK constraints on workspace_id for existing models
- sso_id column on users table
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "012_org_rbac_api_keys"
down_revision = "011_rename_qdrant_collection"
branch_labels = None
depends_on = None

_DEFAULT_UUID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    # 1. Create workspace_role enum (SQLite uses VARCHAR with check)
    # For PostgreSQL, use sa.Enum with create_type=True
    # For SQLite (tests), it's just a VARCHAR
    workspace_role = sa.Enum("admin", "editor", "viewer", name="workspace_role")
    api_key_scope = sa.Enum("system", "workspace", name="api_key_scope")

    # 2. Add sso_id to users
    op.add_column("users", sa.Column("sso_id", sa.String(255), nullable=True, unique=True))

    # 3. Create organizations table
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False, unique=True),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_orgs_slug_unique", "organizations", ["slug"], unique=True)
    op.create_index("idx_orgs_owner", "organizations", ["owner_id"])

    # 4. Create workspaces table
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_workspaces_org_slug", "workspaces", ["org_id", "slug"], unique=True)
    op.create_index("idx_workspaces_org", "workspaces", ["org_id"])

    # 5. Bootstrap default org and workspace
    op.execute(
        "INSERT INTO organizations (id, name, slug, owner_id, created_at, updated_at, deleted) "
        "VALUES (?, 'Default Organization', 'default', ?, "
        "datetime('now'), datetime('now'), 0)",
        (_DEFAULT_UUID, _DEFAULT_UUID),
    )
    op.execute(
        "INSERT INTO workspaces (id, org_id, name, slug, created_at, updated_at, deleted) "
        "VALUES (?, ?, 'Default Workspace', 'default', "
        "datetime('now'), datetime('now'), 0)",
        (_DEFAULT_UUID, _DEFAULT_UUID),
    )

    # 6. Create workspace_members table
    op.create_table(
        "workspace_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("role", workspace_role, nullable=False, server_default="viewer"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_ws_members_user_workspace", "workspace_members", ["user_id", "workspace_id"], unique=True)
    op.create_index("idx_ws_members_workspace", "workspace_members", ["workspace_id"])

    # 7. Create api_keys table
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("key_prefix", sa.String(12), nullable=False),
        sa.Column("scope", api_key_scope, nullable=False),
        sa.Column("org_id", sa.String(36), nullable=True),
        sa.Column("workspace_id", sa.String(36), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_api_keys_hash", "api_keys", ["key_hash"], unique=True)
    op.create_index("idx_api_keys_created_by", "api_keys", ["created_by"])
    op.create_index("idx_api_keys_workspace", "api_keys", ["workspace_id"])

    # 8. Add FK constraints on workspace_id for existing tables
    # Note: SQLite doesn't support ALTER TABLE ADD CONSTRAINT FOREIGN KEY,
    # so we only add FKs for PostgreSQL. SQLite tests work without FK enforcement.
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect != "sqlite":
        for table_name in [
            "agents",
            "workflows",
            "skills",
            "tools",
            "knowledge_bases",
            "prompts",
            "memory_blocks",
            "memories",
            "knowledge_memories",
        ]:
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
        for table_name in [
            "agents",
            "workflows",
            "skills",
            "tools",
            "knowledge_bases",
            "prompts",
            "memory_blocks",
            "memories",
            "knowledge_memories",
        ]:
            op.drop_constraint(f"fk_{table_name}_workspace_id", table_name, type_="foreignkey")

    # Drop tables in reverse order
    op.drop_table("api_keys")
    op.drop_table("workspace_members")
    op.drop_table("workspaces")
    op.drop_table("organizations")

    # Remove sso_id from users
    op.drop_column("users", "sso_id")
