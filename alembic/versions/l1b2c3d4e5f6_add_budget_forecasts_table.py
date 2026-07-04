"""add_budget_forecasts_table

Revision ID: l1b2c3d4e5f6
Revises: k1a2b3c4d5e6
Create Date: 2026-07-03 19:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "l1b2c3d4e5f6"
down_revision: str | None = "k1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "budget_forecasts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("daily_cost", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("daily_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("daily_output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("workspace_id", sa.Uuid(), nullable=False, server_default="00000000-0000-0000-0000-000000000000"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_budget_forecasts_scope", "budget_forecasts", ["scope", "scope_id", "date"], unique=True)
    op.create_index("idx_budget_forecasts_workspace", "budget_forecasts", ["workspace_id", "deleted"])


def downgrade() -> None:
    op.drop_index("idx_budget_forecasts_workspace", table_name="budget_forecasts")
    op.drop_index("idx_budget_forecasts_scope", table_name="budget_forecasts")
    op.drop_table("budget_forecasts")
