"""add_model_deployments_table

Revision ID: m1c2d3e4f5a6
Revises: l1b2c3d4e5f6
Create Date: 2026-07-04 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "m1c2d3e4f5a6"
down_revision: str | None = "l1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_deployments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.String(255), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("version", sa.String(50), nullable=True),
        sa.Column("deployment_config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("approval_status", sa.String(20), nullable=False, server_default="approved"),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("deprecated_at", sa.DateTime(), nullable=True),
        sa.Column("sunset_at", sa.DateTime(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("workspace_id", sa.Uuid(), nullable=False, server_default="00000000-0000-0000-0000-000000000000"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_model_deployments_model_channel",
        "model_deployments",
        ["model_id", "channel", "deleted", "deleted_at"],
        unique=True,
    )
    op.create_index("idx_model_deployments_model", "model_deployments", ["model_id", "deleted"])
    op.create_index("idx_model_deployments_channel", "model_deployments", ["channel", "deleted"])


def downgrade() -> None:
    op.drop_index("idx_model_deployments_channel", table_name="model_deployments")
    op.drop_index("idx_model_deployments_model", table_name="model_deployments")
    op.drop_index("uq_model_deployments_model_channel", table_name="model_deployments")
    op.drop_table("model_deployments")
