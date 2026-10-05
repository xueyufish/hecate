"""add trust_roots for managed runner enrollment (step6/7)

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-10-04

One workspace-scoped named trust material per row (single writer:
TrustRootRegistry). The raw HMAC secret is never stored — only its
SHA-256 digest; the material is provisioned to the host out of band.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "b3c4d5e6f7a8"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trust_roots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("material_digest", sa.String(128), nullable=False),
        sa.Column("issuer_domain", sa.String(256), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("config_fingerprint", sa.String(128), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_trust_roots_workspace_id_workspaces"),
    )
    op.create_index("uq_trust_roots_workspace_name", "trust_roots", ["workspace_id", "name"], unique=True)
    op.create_index("ix_trust_roots_workspace_id", "trust_roots", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_trust_roots_workspace_id", table_name="trust_roots")
    op.drop_index("uq_trust_roots_workspace_name", table_name="trust_roots")
    op.drop_table("trust_roots")
