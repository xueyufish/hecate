"""Add IM identity bindings, binding tokens, and source_channel columns.

Revision ID: a5b6c7d8e9f0
Revises: z4f5a6b7c8d9
Create Date: 2026-08-13

Adds the data-model extensions required by the multi-channel-feishu-slack
OpenSpec change:

- New table ``im_identity_bindings`` — IM user ↔ Hecate user mapping per
  workspace. Carries the unique active-binding key
  ``(workspace_id, channel_type, im_app_id, im_user_id, deleted, deleted_at)``.
- New table ``im_binding_tokens`` — one-time short-lived tokens for the
  binding confirmation workflow.
- ``conversations.source_channel`` (String(32), nullable) — identifies which
  IM platform originated the conversation (``"feishu"`` / ``"slack"`` /
  ``NULL`` for OpenAI-compatible API path).
- ``conversations.im_chat_id`` (String(128), nullable) — caches the IM
  chat identifier for reply routing.
- ``messages.source_channel`` (String(32), nullable).
- ``sessions.source_channel`` (String(32), nullable).

All new columns are nullable with no server default, so the migration is
backwards-compatible with existing data. Pre-existing rows receive NULL on
the new columns.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "a5b6c7d8e9f0"
down_revision = "z4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create IM binding tables and add source_channel columns."""

    op.create_table(
        "im_identity_bindings",
        sa.Column("id", sa.Uuid(), primary_key=True, default=uuid.uuid4),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
            server_default="00000000-0000-0000-0000-000000000000",
        ),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("channel_type", sa.String(length=32), nullable=False),
        sa.Column("im_app_id", sa.String(length=128), nullable=False),
        sa.Column("im_user_id", sa.String(length=128), nullable=False),
        sa.Column(
            "unbound_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_im_identity_bindings_user",
        "im_identity_bindings",
        ["user_id", "deleted"],
    )
    op.create_index(
        "idx_im_identity_bindings_workspace",
        "im_identity_bindings",
        ["workspace_id", "deleted"],
    )
    op.create_index(
        "idx_im_identity_bindings_active",
        "im_identity_bindings",
        ["workspace_id", "channel_type", "im_app_id", "im_user_id", "unbound_at"],
    )

    op.create_table(
        "im_binding_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True, default=uuid.uuid4),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
            server_default="00000000-0000-0000-0000-000000000000",
        ),
        sa.Column("channel_type", sa.String(length=32), nullable=False),
        sa.Column("im_app_id", sa.String(length=128), nullable=False),
        sa.Column("im_user_id", sa.String(length=128), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bound_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_im_binding_tokens_workspace",
        "im_binding_tokens",
        ["workspace_id", "deleted"],
    )
    op.create_index(
        "idx_im_binding_tokens_lookup",
        "im_binding_tokens",
        ["channel_type", "im_app_id", "im_user_id", "deleted"],
    )

    op.add_column(
        "conversations",
        sa.Column("source_channel", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("im_chat_id", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "idx_conversations_source_channel",
        "conversations",
        ["workspace_id", "source_channel", "deleted"],
    )

    op.add_column(
        "messages",
        sa.Column("source_channel", sa.String(length=32), nullable=True),
    )
    op.create_index(
        "idx_messages_source_channel",
        "messages",
        ["workspace_id", "source_channel", "deleted"],
    )

    op.add_column(
        "sessions",
        sa.Column("source_channel", sa.String(length=32), nullable=True),
    )
    op.create_index(
        "idx_sessions_source_channel",
        "sessions",
        ["workspace_id", "source_channel", "deleted"],
    )


def downgrade() -> None:
    """Revert the IM channel data-model additions."""

    op.drop_index("idx_sessions_source_channel", table_name="sessions")
    op.drop_column("sessions", "source_channel")

    op.drop_index("idx_messages_source_channel", table_name="messages")
    op.drop_column("messages", "source_channel")

    op.drop_index("idx_conversations_source_channel", table_name="conversations")
    op.drop_column("conversations", "im_chat_id")
    op.drop_column("conversations", "source_channel")

    op.drop_index("idx_im_binding_tokens_lookup", table_name="im_binding_tokens")
    op.drop_index("idx_im_binding_tokens_workspace", table_name="im_binding_tokens")
    op.drop_table("im_binding_tokens")

    op.drop_index("idx_im_identity_bindings_active", table_name="im_identity_bindings")
    op.drop_index("idx_im_identity_bindings_workspace", table_name="im_identity_bindings")
    op.drop_index("idx_im_identity_bindings_user", table_name="im_identity_bindings")
    op.drop_table("im_identity_bindings")
