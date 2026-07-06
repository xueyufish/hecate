"""add model_metadata column to model_registry

Revision ID: q5f6a7b8c9d0
Revises: p4f5a6b7c8d9
Create Date: 2026-07-06 12:00:00.000000
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "q5f6a7b8c9d0"
down_revision: str = "p4f5a6b7c8d9"
branch_labels: str | None = None
depends_on: str | None = None


_DEFAULT_METADATA = {
    "modalities": {"input": ["text"], "output": ["text"]},
    "capabilities": {},
    "limits": {},
}


def upgrade() -> None:
    """Add model_metadata JSON column and backfill existing rows."""
    op.add_column("model_registry", sa.Column("model_metadata", sa.JSON(), nullable=True))

    # Backfill existing rows with conservative defaults based on model_type
    conn = op.get_bind()
    results = conn.execute(sa.text("SELECT id, model_type FROM model_registry WHERE deleted = 0")).fetchall()
    for row in results:
        meta = dict(_DEFAULT_METADATA)
        if row[1] == "embedding":
            meta["modalities"] = {"input": ["text"], "output": ["embedding"]}
        conn.execute(
            sa.text("UPDATE model_registry SET model_metadata = :meta WHERE id = :id"),
            {"meta": json.dumps(meta), "id": row[0]},
        )

    # Make column non-nullable after backfill
    op.alter_column("model_registry", "model_metadata", nullable=False, server_default="{}")


def downgrade() -> None:
    """Remove model_metadata column."""
    op.drop_column("model_registry", "model_metadata")
