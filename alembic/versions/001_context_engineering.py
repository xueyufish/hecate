"""Add evidences and budget_snapshots tables.

Revision ID: 001_context_engineering
Revises:
Create Date: 2026-05-27
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "001_context_engineering"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create evidences and budget_snapshots tables."""

    op.create_table(
        "evidences",
        sa.Column("id", sa.Uuid(), primary_key=True, default=uuid.uuid4),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("tool_name", sa.String(255), nullable=False),
        sa.Column("tool_arguments", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("raw_content", sa.Text(), nullable=True),
        sa.Column("normalized_content", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("is_error", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("importance", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("source_type", sa.String(50), nullable=False, server_default="tool"),
        sa.Column("provenance", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_evidences_session", "evidences", ["session_id"])
    op.create_index("idx_evidences_tool", "evidences", ["tool_name"])
    op.create_index("idx_evidences_importance", "evidences", ["importance"])

    op.create_table(
        "budget_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True, default=uuid.uuid4),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("total_budget", sa.Integer(), nullable=False),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_remaining", sa.Integer(), nullable=False),
        sa.Column("degradation_level", sa.String(20), nullable=False, server_default="none"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_budget_snapshots_session", "budget_snapshots", ["session_id"])


def downgrade() -> None:
    """Drop evidences and budget_snapshots tables."""

    op.drop_index("idx_budget_snapshots_session", table_name="budget_snapshots")
    op.drop_table("budget_snapshots")

    op.drop_index("idx_evidences_importance", table_name="evidences")
    op.drop_index("idx_evidences_tool", table_name="evidences")
    op.drop_index("idx_evidences_session", table_name="evidences")
    op.drop_table("evidences")
