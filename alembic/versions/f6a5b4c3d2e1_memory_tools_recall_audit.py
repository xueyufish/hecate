"""memory tools: revision columns, recall_messages, memory_edit_log

Revision ID: a1b2c3d4e5f6
Revises: p19a0b3c4d5e
Create Date: 2026-09-19

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "f6a5b4c3d2e1"
down_revision = "p19a0b3c4d5e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add memory revision columns and the recall/audit tables."""
    # Optimistic-concurrency counters on the three memory stores.
    op.add_column(
        "memory_blocks",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "memories",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "knowledge_memories",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )

    # Conversation recall storage — transcript-level index rows (vectors in
    # Qdrant `hecate_recall`). Outlives event retention; cascade-deleted with
    # the owning conversation by the application layer.
    op.create_table(
        "recall_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("event_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id",
            "content_hash",
            "seq",
            name="uq_recall_messages_session_hash_seq",
        ),
    )
    op.create_index("idx_recall_messages_workspace", "recall_messages", ["workspace_id", "deleted"])
    op.create_index("idx_recall_messages_agent", "recall_messages", ["workspace_id", "agent_id"])
    op.create_index("idx_recall_messages_session", "recall_messages", ["session_id", "seq"])
    op.create_index("idx_recall_messages_conversation", "recall_messages", ["conversation_id"])

    # Append-only audit trail for agent-driven memory mutations.
    op.create_table(
        "memory_edit_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("tool_name", sa.String(50), nullable=False),
        sa.Column("target_type", sa.String(30), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("revision_before", sa.Integer(), nullable=True),
        sa.Column("revision_after", sa.Integer(), nullable=True),
        sa.Column("before_summary", sa.Text(), nullable=True),
        sa.Column("after_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_memory_edit_log_workspace", "memory_edit_log", ["workspace_id", "created_at"])
    op.create_index("idx_memory_edit_log_target", "memory_edit_log", ["target_type", "target_id"])
    op.create_index("idx_memory_edit_log_agent", "memory_edit_log", ["workspace_id", "agent_id"])


def downgrade() -> None:
    """Reverse the memory tools migration."""
    op.drop_index("idx_memory_edit_log_agent", "memory_edit_log")
    op.drop_index("idx_memory_edit_log_target", "memory_edit_log")
    op.drop_index("idx_memory_edit_log_workspace", "memory_edit_log")
    op.drop_table("memory_edit_log")

    op.drop_index("idx_recall_messages_conversation", "recall_messages")
    op.drop_index("idx_recall_messages_session", "recall_messages")
    op.drop_index("idx_recall_messages_agent", "recall_messages")
    op.drop_index("idx_recall_messages_workspace", "recall_messages")
    op.drop_table("recall_messages")

    op.drop_column("knowledge_memories", "revision")
    op.drop_column("memories", "revision")
    op.drop_column("memory_blocks", "revision")
