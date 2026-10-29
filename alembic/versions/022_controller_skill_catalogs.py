"""Add the frozen skill catalog for controller-loop tasks.

Revision ID: 022_controller_skill_catalogs
Revises: 021_approval_decisions

Expand-only: no existing table is touched. One row per explicitly
enabled controller task, written before its first model call; unique
per task so a racing or recovering dispatch reuses the frozen surface
instead of re-rendering it. The rendered catalog, the advertised L2
membership, the version pins, unversioned content snapshots and the
payload digest bind what actually reached the model input.
"""

import sqlalchemy as sa
from alembic import op

revision = "022_controller_skill_catalogs"
down_revision = "021_approval_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add controller skill catalog storage without altering existing tables."""
    op.create_table(
        "controller_skill_catalogs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("agent_version_id", sa.Uuid(), nullable=True),
        sa.Column("catalog_rendered", sa.Text(), nullable=False),
        sa.Column("advertised_names", sa.JSON(), nullable=False),
        sa.Column("pins", sa.JSON(), nullable=False),
        sa.Column("unversioned_snapshots", sa.JSON(), nullable=False),
        sa.Column("budgets", sa.JSON(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_controller_skill_catalogs")),
        sa.UniqueConstraint("task_id", name=op.f("uq_controller_skill_catalogs_task_id")),
    )


def downgrade() -> None:
    """Frozen catalogs are recovery state; dropping them loses the binding."""
    if op.get_bind().execute(sa.text("SELECT 1 FROM controller_skill_catalogs LIMIT 1")).first() is not None:
        raise RuntimeError("Cannot downgrade: frozen skill catalogs must be retained")
    op.drop_table("controller_skill_catalogs")
