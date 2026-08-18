"""Add plugin/skill provenance columns for Agent Plugins ingestion.

Revision ID: c6d7e8f9a0b1
Revises: a5b6c7d8e9f0
Create Date: 2026-08-17

Adds the data-model extensions required by the agent-plugins-ingestion
OpenSpec change (feature 5.5c):

- ``plugins.origin`` (String(1024), nullable) — install source descriptor
  for Agent Plugins packages (includes the git pin triple when applicable).
- ``plugins.content_hash`` (String(128), nullable) — content digest of the
  materialized package tree (pin-by-hash).
- ``plugins.scan_result`` (JSON, nullable) — reserved for feature 5.13a
  install-time content scanning; NULL until that feature lands.
- ``skills.origin`` (String(1024), nullable) — package origin for
  plugin-derived skills.
- ``skills.plugin_id`` (UUID, nullable, FK → plugins.id ON DELETE CASCADE) —
  owning package for skills imported by the ingestion pipeline.

All new columns are nullable with no server default, so the migration is
backwards-compatible with existing data. Pre-existing rows receive NULL.

Note: the migration chain has a second pre-existing head (``b1b2c3d4e5f6``,
DLP chain, forked before this change). This revision chains from the newer
``a5b6c7d8e9f0`` head; reconciling the fork is out of scope here.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "c6d7e8f9a0b1"
down_revision = "a5b6c7d8e9f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("plugins") as batch:
        batch.add_column(sa.Column("origin", sa.String(1024), nullable=True))
        batch.add_column(sa.Column("content_hash", sa.String(128), nullable=True))
        batch.add_column(sa.Column("scan_result", sa.JSON(), nullable=True))

    with op.batch_alter_table("skills") as batch:
        batch.add_column(sa.Column("origin", sa.String(1024), nullable=True))
        batch.add_column(
            sa.Column(
                "plugin_id",
                sa.Uuid(),
                sa.ForeignKey("plugins.id", ondelete="CASCADE"),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("skills") as batch:
        batch.drop_column("plugin_id")
        batch.drop_column("origin")

    with op.batch_alter_table("plugins") as batch:
        batch.drop_column("scan_result")
        batch.drop_column("content_hash")
        batch.drop_column("origin")
