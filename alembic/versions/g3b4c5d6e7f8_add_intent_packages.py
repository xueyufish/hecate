"""intent packages: categories, samples, named versions (6.49)

Revision ID: g3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-09-13

Adds (Intent Recognition 6.23 ⊕ 1.3.10 + Intent Packages 6.49):
- ``intent_packages`` — named package, workspace-scoped
- ``intent_package_categories`` — intent categories (name, description,
  optional domain label, optional policy-gated flag)
- ``intent_package_samples`` — sample utterances with provenance JSON
- ``intent_package_versions`` — named, immutable freezes of the draft
  content with a canonical content hash; ``(package_id, name)`` is unique
  across soft-deleted rows too, so a version name always refers to the same
  frozen content. ``published_at`` null marks unpublished drafts of a
  version; runtime evidence reads published versions only.

All changes are additive; existing rows are unaffected.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "g3b4c5d6e7f8"
down_revision: str | None = "f2a3b4c5d6e7"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "intent_packages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_intent_packages_workspace",
        "intent_packages",
        ["workspace_id", "deleted"],
    )
    op.create_index("idx_intent_packages_created", "intent_packages", ["created_at"])

    op.create_table(
        "intent_package_categories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("package_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("domain", sa.String(255), nullable=True),
        sa.Column("policy_gated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_intent_package_categories_package",
        "intent_package_categories",
        ["package_id", "deleted"],
    )

    op.create_table(
        "intent_package_samples",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("utterance", sa.Text(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=True, server_default="{}"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_intent_package_samples_category",
        "intent_package_samples",
        ["category_id", "deleted"],
    )

    op.create_table(
        "intent_package_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("package_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("content", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gate_report", sa.JSON(), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("package_id", "name", name="uq_intent_package_versions_name"),
    )
    op.create_index(
        "idx_intent_package_versions_package",
        "intent_package_versions",
        ["package_id", "deleted"],
    )
    op.create_index(
        "idx_intent_package_versions_workspace",
        "intent_package_versions",
        ["workspace_id", "deleted"],
    )


def downgrade() -> None:
    op.drop_index("idx_intent_package_versions_workspace", table_name="intent_package_versions")
    op.drop_index("idx_intent_package_versions_package", table_name="intent_package_versions")
    op.drop_table("intent_package_versions")
    op.drop_index("idx_intent_package_samples_category", table_name="intent_package_samples")
    op.drop_table("intent_package_samples")
    op.drop_index("idx_intent_package_categories_package", table_name="intent_package_categories")
    op.drop_table("intent_package_categories")
    op.drop_index("idx_intent_packages_created", table_name="intent_packages")
    op.drop_index("idx_intent_packages_workspace", table_name="intent_packages")
    op.drop_table("intent_packages")
