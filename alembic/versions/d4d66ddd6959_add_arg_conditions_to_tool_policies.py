"""add arg_conditions to tool_policies

Revision ID: d4d66ddd6959
Revises: f1a2b3c4d5e6
Create Date: 2026-06-19 21:53:21.723867

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4d66ddd6959"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tool_policies", sa.Column("arg_conditions", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("tool_policies", "arg_conditions")
