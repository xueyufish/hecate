"""add_user_scim_fields

Revision ID: k1a2b3c4d5e6
Revises: j1e2f3a4b5c6
Create Date: 2026-07-03 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "k1a2b3c4d5e6"
down_revision: str | None = "j1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add SCIM-related fields to users table
    op.add_column("users", sa.Column("external_id", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("display_name", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("given_name", sa.String(128), nullable=True))
    op.add_column("users", sa.Column("family_name", sa.String(128), nullable=True))
    op.add_column("users", sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")))

    # Create index on external_id for SCIM lookups
    op.create_index("ix_users_external_id", "users", ["external_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_users_external_id", table_name="users")
    op.drop_column("users", "active")
    op.drop_column("users", "family_name")
    op.drop_column("users", "given_name")
    op.drop_column("users", "display_name")
    op.drop_column("users", "external_id")
