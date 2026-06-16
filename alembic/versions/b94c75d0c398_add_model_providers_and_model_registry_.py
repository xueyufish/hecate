"""add model_providers and model_registry tables

Revision ID: b94c75d0c398
Revises: 005_users
Create Date: 2026-05-29 17:16:43.756717

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b94c75d0c398"
down_revision: str | None = "005_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_providers",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("api_key_encrypted", sa.String(1024), nullable=False),
        sa.Column("base_url", sa.String(512), nullable=True),
        sa.Column("config", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("status", sa.String(20), nullable=False, server_default="inactive"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_model_providers_name",
        "model_providers",
        ["name"],
        postgresql_where="(deleted_at IS NULL)",
    )

    op.create_table(
        "model_registry",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("provider_id", sa.UUID(), sa.ForeignKey("model_providers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_id", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("model_type", sa.String(50), nullable=False, server_default="chat"),
        sa.Column("capabilities", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("max_context", sa.Integer(), nullable=True),
        sa.Column("is_custom", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_model_registry_provider",
        "model_registry",
        ["provider_id"],
        postgresql_where="(deleted_at IS NULL)",
    )
    op.create_index(
        "idx_model_registry_model_id",
        "model_registry",
        ["model_id"],
        postgresql_where="(deleted_at IS NULL)",
    )


def downgrade() -> None:
    op.drop_index("idx_model_registry_model_id", table_name="model_registry")
    op.drop_index("idx_model_registry_provider", table_name="model_registry")
    op.drop_table("model_registry")
    op.drop_index("idx_model_providers_name", table_name="model_providers")
    op.drop_table("model_providers")
