"""add_i18n_locale_fields

Revision ID: j1e2f3a4b5c6
Revises: i0d1e2f3a4b5
Create Date: 2026-07-03 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "j1e2f3a4b5c6"
down_revision: str | None = "i0d1e2f3a4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add preferred_locale to users table
    op.add_column("users", sa.Column("preferred_locale", sa.String(10), nullable=True))

    # Add default_locale to workspaces table
    op.add_column("workspaces", sa.Column("default_locale", sa.String(10), nullable=False, server_default="en"))


def downgrade() -> None:
    op.drop_column("workspaces", "default_locale")
    op.drop_column("users", "preferred_locale")
