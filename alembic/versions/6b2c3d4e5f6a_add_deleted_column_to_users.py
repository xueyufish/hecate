"""add deleted column to users

Revision ID: 6b2c3d4e5f6a
Revises: 5a1b2c3d4e5f
Create Date: 2026-08-20

Repairs a schema/ORM drift in the ``users`` table. The original
``005_users`` migration created the table with only ``deleted_at``;
``UserModel`` inherits from :class:`BaseModel`, which declares both
``deleted`` (Boolean, nullable=False) and ``deleted_at``. Any ORM
query against ``users`` that goes through the default filter
(``WHERE deleted = false``) raises
``UndefinedColumnError: column users.deleted does not exist``. Adding
the missing ``deleted`` column with a ``false`` server default aligns
the table with the ORM and unblocks user listing / RBAC / API key
lookups that previously crashed at first touch.

The same root cause and fix shape apply to ``plugins`` (handled in
``5a1b2c3d4e5f_add_deleted_column_to_plugins``); this migration
addresses the symmetric gap on ``users``.

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "6b2c3d4e5f6a"
down_revision: str | None = "5a1b2c3d4e5f"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add the missing ``deleted`` boolean column to ``users``."""
    op.add_column(
        "users",
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "deleted")
