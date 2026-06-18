"""add_available_when_to_tools

Revision ID: e0e69d57120a
Revises: 017_observability
Create Date: 2026-06-18 22:13:40.595465

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e0e69d57120a"
down_revision: str | None = "017_observability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tools", sa.Column("available_when", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("tools", "available_when")
