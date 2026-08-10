"""add dlp_policies / dlp_custom_regex / dlp_dictionaries tables

Revision ID: a1b2c3d4e5f6
Revises: z4f5a6b7c8d9
Create Date: 2026-08-10

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "z4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create DLP tables for policies, custom regex patterns, and dictionaries."""
    op.create_table(
        "dlp_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("entity_type", sa.String(length=100), nullable=False),
        sa.Column("direction", sa.String(length=50), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("mask_format", sa.String(length=100), nullable=True),
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dlp_policies_org_id", "dlp_policies", ["org_id"])
    op.create_index("ix_dlp_policies_workspace_id", "dlp_policies", ["workspace_id"])
    op.create_index("ix_dlp_policies_agent_id", "dlp_policies", ["agent_id"])
    op.create_index("ix_dlp_policies_entity_type", "dlp_policies", ["entity_type"])
    op.create_index("ix_dlp_policies_direction", "dlp_policies", ["direction"])
    op.create_index("ix_dlp_policies_org_entity", "dlp_policies", ["org_id", "entity_type", "direction"])
    op.create_index(
        "ix_dlp_policies_workspace_entity",
        "dlp_policies",
        ["workspace_id", "entity_type", "direction"],
    )
    op.create_index(
        "ix_dlp_policies_agent_entity",
        "dlp_policies",
        ["agent_id", "entity_type", "direction"],
    )

    op.create_table(
        "dlp_custom_regex",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.String(length=100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dlp_custom_regex_org_id", "dlp_custom_regex", ["org_id"])
    op.create_index("ix_dlp_custom_regex_workspace_id", "dlp_custom_regex", ["workspace_id"])
    op.create_index("ix_dlp_custom_regex_entity_type", "dlp_custom_regex", ["entity_type"])
    op.create_index(
        "ix_dlp_custom_regex_org_entity",
        "dlp_custom_regex",
        ["org_id", "entity_type"],
    )

    op.create_table(
        "dlp_dictionaries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=100), nullable=False),
        sa.Column("case_sensitive", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("terms", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dlp_dictionaries_org_id", "dlp_dictionaries", ["org_id"])
    op.create_index("ix_dlp_dictionaries_workspace_id", "dlp_dictionaries", ["workspace_id"])
    op.create_index("ix_dlp_dictionaries_entity_type", "dlp_dictionaries", ["entity_type"])
    op.create_index(
        "ix_dlp_dictionaries_org_entity",
        "dlp_dictionaries",
        ["org_id", "entity_type"],
    )


def downgrade() -> None:
    """Drop DLP tables."""
    op.drop_index("ix_dlp_dictionaries_org_entity", table_name="dlp_dictionaries")
    op.drop_index("ix_dlp_dictionaries_entity_type", table_name="dlp_dictionaries")
    op.drop_index("ix_dlp_dictionaries_workspace_id", table_name="dlp_dictionaries")
    op.drop_index("ix_dlp_dictionaries_org_id", table_name="dlp_dictionaries")
    op.drop_table("dlp_dictionaries")

    op.drop_index("ix_dlp_custom_regex_org_entity", table_name="dlp_custom_regex")
    op.drop_index("ix_dlp_custom_regex_entity_type", table_name="dlp_custom_regex")
    op.drop_index("ix_dlp_custom_regex_workspace_id", table_name="dlp_custom_regex")
    op.drop_index("ix_dlp_custom_regex_org_id", table_name="dlp_custom_regex")
    op.drop_table("dlp_custom_regex")

    op.drop_index("ix_dlp_policies_agent_entity", table_name="dlp_policies")
    op.drop_index("ix_dlp_policies_workspace_entity", table_name="dlp_policies")
    op.drop_index("ix_dlp_policies_org_entity", table_name="dlp_policies")
    op.drop_index("ix_dlp_policies_direction", table_name="dlp_policies")
    op.drop_index("ix_dlp_policies_entity_type", table_name="dlp_policies")
    op.drop_index("ix_dlp_policies_agent_id", table_name="dlp_policies")
    op.drop_index("ix_dlp_policies_workspace_id", table_name="dlp_policies")
    op.drop_index("ix_dlp_policies_org_id", table_name="dlp_policies")
    op.drop_table("dlp_policies")
