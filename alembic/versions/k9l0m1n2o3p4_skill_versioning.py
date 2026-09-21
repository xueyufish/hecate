"""skill versioning: skill_versions table (5.9d)

Revision ID: k9l0m1n2o3p4
Revises: a8b9c0d1e2f4
Create Date: 2026-09-21

Adds (Skill Versioning 5.9d):
- ``skill_versions`` — immutable snapshots of a skill's content fields
  (name, instructions, allowed_tools, scripts, references, description,
  max_tokens). ``(skill_id, version)`` is unique; version numbers are
  monotonically increasing per skill. Deleting the live skill row does
  not cascade here: agent snapshots pin specific versions and must keep
  resolving after the source row is gone.

Purely additive; existing rows are unaffected. No backfill — existing
skills stay version-less until their first explicit commit.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "k9l0m1n2o3p4"
down_revision: str | None = "a8b9c0d1e2f4"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "skill_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False, server_default=""),
        sa.Column("change_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("config_snapshot", sa.JSON(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("learned_run_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"]),
    )
    op.create_index(
        "uq_skill_versions_skill_version",
        "skill_versions",
        ["skill_id", "version"],
        unique=True,
    )
    op.create_index("idx_skill_versions_workspace", "skill_versions", ["workspace_id", "deleted"])


def downgrade() -> None:
    op.drop_index("idx_skill_versions_workspace", table_name="skill_versions")
    op.drop_index("uq_skill_versions_skill_version", table_name="skill_versions")
    op.drop_table("skill_versions")
