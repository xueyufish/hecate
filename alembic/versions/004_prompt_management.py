"""Add prompts and prompt_versions tables.

Revision ID: 004_prompt_management
Revises: 003_memory_system
Create Date: 2026-05-28
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "004_prompt_management"
down_revision = "003_memory_system"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create prompts and prompt_versions tables."""

    op.create_table(
        "prompts",
        sa.Column("id", sa.Uuid(), primary_key=True, default=uuid.uuid4),
        sa.Column("workspace_id", sa.Uuid(), nullable=False, server_default="00000000-0000-0000-0000-000000000000"),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_prompts_workspace", "prompts", ["workspace_id"])
    op.create_index("idx_prompts_name", "prompts", ["workspace_id", "name"], unique=True)

    op.create_table(
        "prompt_versions",
        sa.Column("id", sa.Uuid(), primary_key=True, default=uuid.uuid4),
        sa.Column("prompt_id", sa.Uuid(), sa.ForeignKey("prompts.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("variables", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("labels", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_prompt_versions_prompt", "prompt_versions", ["prompt_id"])
    op.create_index("idx_prompt_versions_unique", "prompt_versions", ["prompt_id", "version"], unique=True)


def downgrade() -> None:
    """Drop prompt_versions and prompts tables."""

    op.drop_index("idx_prompt_versions_unique", table_name="prompt_versions")
    op.drop_index("idx_prompt_versions_prompt", table_name="prompt_versions")
    op.drop_table("prompt_versions")

    op.drop_index("idx_prompts_name", table_name="prompts")
    op.drop_index("idx_prompts_workspace", table_name="prompts")
    op.drop_table("prompts")
