"""Add users table for email/password authentication.

Revision ID: 005_users
Revises: 004_prompt_management
Create Date: 2026-05-29
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "005_users"
down_revision = "004_prompt_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create users table."""

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True, default=uuid.uuid4),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_users_email_unique", "users", ["email"], unique=True)


def downgrade() -> None:
    """Drop users table."""

    op.drop_index("idx_users_email_unique", table_name="users")
    op.drop_table("users")
