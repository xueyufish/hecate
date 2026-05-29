"""Create all P1 core tables.

Revision ID: 000_base
Revises:
Create Date: 2026-05-29
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "000_base"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create agents, conversations, sessions, messages, knowledge_bases, documents, checkpoints, tools, skills."""

    # --- agents ---
    op.create_table(
        "agents",
        sa.Column("id", sa.Uuid(), primary_key=True, default=uuid.uuid4),
        sa.Column("workspace_id", sa.Uuid(), nullable=False, server_default="00000000-0000-0000-0000-000000000000"),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("persona", sa.Text(), nullable=True),
        sa.Column("model_config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("mode", sa.String(50), nullable=False, server_default="chat"),
        sa.Column("workflow_id", sa.Uuid(), nullable=True),
        sa.Column("tools", sa.JSON(), server_default="[]"),
        sa.Column("skills", sa.JSON(), server_default="[]"),
        sa.Column("knowledge_base_ids", sa.JSON(), server_default="[]"),
        sa.Column("risk_level", sa.String(20), server_default="LOW"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_agents_workspace",
        "agents",
        ["workspace_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # --- conversations ---
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), primary_key=True, default=uuid.uuid4),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_conversations_agent",
        "conversations",
        ["agent_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # --- sessions ---
    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), primary_key=True, default=uuid.uuid4),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("current_node", sa.String(100), nullable=True),
        sa.Column("checkpoint_id", sa.Uuid(), nullable=True),
        sa.Column("metadata", sa.JSON(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_sessions_agent", "sessions", ["agent_id"])
    op.create_index("idx_sessions_conversation", "sessions", ["conversation_id"])

    # --- messages ---
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), primary_key=True, default=uuid.uuid4),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tool_calls", sa.JSON(), nullable=True),
        sa.Column("tool_call_id", sa.String(100), nullable=True),
        sa.Column("metadata", sa.JSON(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_messages_conversation", "messages", ["conversation_id", "created_at"])

    # --- knowledge_bases ---
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.Uuid(), primary_key=True, default=uuid.uuid4),
        sa.Column("workspace_id", sa.Uuid(), nullable=False, server_default="00000000-0000-0000-0000-000000000000"),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("embedding_model", sa.String(100), nullable=False, server_default="BAAI/bge-m3"),
        sa.Column("chunk_strategy", sa.String(20), nullable=False, server_default="fixed"),
        sa.Column("chunk_size", sa.Integer(), nullable=False, server_default="512"),
        sa.Column("chunk_overlap", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("qdrant_collection", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    # --- documents ---
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True, default=uuid.uuid4),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("file_size", sa.Integer(), server_default="0"),
        sa.Column("content_type", sa.String(100), nullable=True),
        sa.Column("parsing_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("parsing_error", sa.Text(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_documents_kb",
        "documents",
        ["knowledge_base_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # --- checkpoints (no updated_at, no deleted_at — immutable) ---
    op.create_table(
        "checkpoints",
        sa.Column("id", sa.Uuid(), primary_key=True, default=uuid.uuid4),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("superstep", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(100), nullable=True),
        sa.Column("channel_state", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("pending_writes", sa.JSON(), server_default="[]"),
        sa.Column("metadata", sa.JSON(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_checkpoints_session", "checkpoints", ["session_id", "superstep"])

    # --- tools ---
    op.create_table(
        "tools",
        sa.Column("id", sa.Uuid(), primary_key=True, default=uuid.uuid4),
        sa.Column("workspace_id", sa.Uuid(), nullable=False, server_default="00000000-0000-0000-0000-000000000000"),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("returns", sa.JSON(), nullable=True),
        sa.Column("risk_level", sa.String(20), server_default="LOW"),
        sa.Column("approval_required", sa.Boolean(), server_default="false"),
        sa.Column("sandbox_enabled", sa.Boolean(), server_default="false"),
        sa.Column("sandbox_config", sa.JSON(), server_default="{}"),
        sa.Column("mcp_server", sa.String(255), nullable=True),
        sa.Column("mcp_tool_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_tools_workspace_name",
        "tools",
        ["workspace_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # --- skills ---
    op.create_table(
        "skills",
        sa.Column("id", sa.Uuid(), primary_key=True, default=uuid.uuid4),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("allowed_tools", sa.JSON(), server_default="[]"),
        sa.Column("metadata", sa.JSON(), server_default="{}"),
        sa.Column("scripts", sa.JSON(), server_default="[]"),
        sa.Column("references", sa.JSON(), server_default="[]"),
        sa.Column("max_tokens", sa.Integer(), server_default="2000"),
        sa.Column("auto_load", sa.Boolean(), server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_skills_name",
        "skills",
        ["name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Drop all core tables in reverse dependency order."""

    op.drop_index("idx_skills_name", table_name="skills")
    op.drop_table("skills")

    op.drop_index("idx_tools_workspace_name", table_name="tools")
    op.drop_table("tools")

    op.drop_index("idx_checkpoints_session", table_name="checkpoints")
    op.drop_table("checkpoints")

    op.drop_index("idx_documents_kb", table_name="documents")
    op.drop_table("documents")

    op.drop_table("knowledge_bases")

    op.drop_index("idx_messages_conversation", table_name="messages")
    op.drop_table("messages")

    op.drop_index("idx_sessions_conversation", table_name="sessions")
    op.drop_index("idx_sessions_agent", table_name="sessions")
    op.drop_table("sessions")

    op.drop_index("idx_conversations_agent", table_name="conversations")
    op.drop_table("conversations")

    op.drop_index("idx_agents_workspace", table_name="agents")
    op.drop_table("agents")
