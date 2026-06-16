"""Create pii_mappings table for encrypted PII storage.

Revision ID: 015_pii_mappings_table
Revises: 014_agent_guardrail_config
Create Date: 2026-06-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "015_pii_mappings_table"
down_revision = "014_agent_guardrail_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pii_mappings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("placeholder", sa.String(255), nullable=False),
        sa.Column("encrypted_value", sa.LargeBinary(), nullable=False),
        sa.Column("pii_type", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("deleted", sa.Boolean(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "placeholder", name="uq_pii_mappings_session_placeholder"),
    )
    op.create_index("idx_pii_mappings_session", "pii_mappings", ["session_id", "deleted"])


def downgrade() -> None:
    op.drop_index("idx_pii_mappings_session", table_name="pii_mappings")
    op.drop_table("pii_mappings")
