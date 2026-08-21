"""add deleted column to plugins

Revision ID: 5a1b2c3d4e5f
Revises: 09cd094a786b
Create Date: 2026-08-20

Repairs a schema/ORM drift in the ``plugins`` table. The original
``v0b1c2d3e4f5`` migration created the table with only ``deleted_at``;
``PluginModel`` inherits from :class:`BaseModel`, which declares both
``deleted`` (Boolean, nullable=False) and ``deleted_at``. Any startup code
that queried ``plugins`` via SQLAlchemy raised
``UndefinedColumnError: column plugins.deleted does not exist``. Adding
the missing ``deleted`` column with a ``false`` server default aligns the
table with the ORM and unblocks plugin discovery.

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "5a1b2c3d4e5f"
down_revision: str | None = "09cd094a786b"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add the missing ``deleted`` boolean column to ``plugins``."""
    op.add_column(
        "plugins",
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("plugins", "deleted")
