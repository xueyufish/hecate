"""add conversation turn scores and clusters tables

Revision ID: u9d0e1f2a3b4
Revises: t8c9d0e1f2a3
Create Date: 2026-07-09 10:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "u9d0e1f2a3b4"
down_revision: str = "t8c9d0e1f2a3"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Create conversation_turn_scores table
    op.create_table(
        "conversation_turn_scores",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("helpfulness", sa.Float(), nullable=True),
        sa.Column("coherence", sa.Float(), nullable=True),
        sa.Column("instruction_adherence", sa.Float(), nullable=True),
        sa.Column("overall_score", sa.Float(), nullable=True),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("topic", sa.String(100), nullable=True),
        sa.Column("user_rating", sa.String(20), nullable=True),
        sa.Column("user_comment", sa.Text(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("feedback_at", sa.DateTime(), nullable=True),
        sa.Column("scored_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_turn_scores_conversation", "conversation_turn_scores", ["conversation_id", "turn_index"])
    op.create_index("idx_turn_scores_message", "conversation_turn_scores", ["message_id"])
    op.create_index("idx_turn_scores_scored", "conversation_turn_scores", ["scored_at"])

    # Create conversation_clusters table
    op.create_table(
        "conversation_clusters",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("centroid_embedding", sa.JSON(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("conversation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dbi_score", sa.Float(), nullable=True),
        sa.Column("silhouette_score", sa.Float(), nullable=True),
        sa.Column("cohesion_score", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_clusters_label", "conversation_clusters", ["label"])
    op.create_index("idx_clusters_count", "conversation_clusters", ["conversation_count"])

    # Add new columns to conversations table
    op.add_column("conversations", sa.Column("quality_score", sa.Float(), nullable=True))
    op.add_column("conversations", sa.Column("quality_min_score", sa.Float(), nullable=True))
    op.add_column("conversations", sa.Column("quality_scored_at", sa.DateTime(), nullable=True))
    op.add_column("conversations", sa.Column("quality_metrics", sa.JSON(), nullable=True))
    op.add_column("conversations", sa.Column("topic", sa.String(100), nullable=True))
    op.add_column("conversations", sa.Column("feedback_summary", sa.JSON(), nullable=True))
    op.add_column("conversations", sa.Column("cluster_id", sa.Uuid(), nullable=True))
    op.create_index("idx_conversations_quality", "conversations", ["quality_score"])
    op.create_index("idx_conversations_topic", "conversations", ["topic"])
    op.create_index("idx_conversations_cluster", "conversations", ["cluster_id"])


def downgrade() -> None:
    op.drop_index("idx_conversations_cluster", table_name="conversations")
    op.drop_index("idx_conversations_topic", table_name="conversations")
    op.drop_index("idx_conversations_quality", table_name="conversations")
    op.drop_column("conversations", "cluster_id")
    op.drop_column("conversations", "feedback_summary")
    op.drop_column("conversations", "topic")
    op.drop_column("conversations", "quality_metrics")
    op.drop_column("conversations", "quality_scored_at")
    op.drop_column("conversations", "quality_min_score")
    op.drop_column("conversations", "quality_score")

    op.drop_index("idx_clusters_count", table_name="conversation_clusters")
    op.drop_index("idx_clusters_label", table_name="conversation_clusters")
    op.drop_table("conversation_clusters")

    op.drop_index("idx_turn_scores_scored", table_name="conversation_turn_scores")
    op.drop_index("idx_turn_scores_message", table_name="conversation_turn_scores")
    op.drop_index("idx_turn_scores_conversation", table_name="conversation_turn_scores")
    op.drop_table("conversation_turn_scores")
