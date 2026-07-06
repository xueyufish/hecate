"""add inference_endpoints table

Revision ID: r6a7b8c9d0e1
Revises: t8c9d0e1f2a3
Create Date: 2026-07-06 14:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "r6a7b8c9d0e1"
down_revision: str = "t8c9d0e1f2a3"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "inference_endpoints",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.String(1024), nullable=False),
        sa.Column("model_id", sa.String(255), nullable=False),
        sa.Column("backend_type", sa.String(50), nullable=False, server_default="openai-compatible"),
        sa.Column("auth_config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("health_status", sa.String(20), nullable=False, server_default="healthy"),
        sa.Column("last_health_at", sa.DateTime(), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_inference_endpoints_model", "inference_endpoints", ["model_id", "deleted"])
    op.create_index("idx_inference_endpoints_workspace", "inference_endpoints", ["workspace_id", "deleted"])


def downgrade() -> None:
    op.drop_index("idx_inference_endpoints_workspace", table_name="inference_endpoints")
    op.drop_index("idx_inference_endpoints_model", table_name="inference_endpoints")
    op.drop_table("inference_endpoints")
