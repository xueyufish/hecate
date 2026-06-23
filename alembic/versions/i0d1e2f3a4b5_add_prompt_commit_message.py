"""Add commit_message to prompt versions.

Revision ID: i0d1e2f3a4b5
Revises: h9c0d1e2f3a4
Create Date: 2026-06-22
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "i0d1e2f3a4b5"
down_revision = "h9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("prompt_versions", sa.Column("commit_message", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("prompt_versions", "commit_message")
