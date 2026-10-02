"""add agent principals and deployments (expand)

Revision ID: 7c3a91b4e2f5
Revises: b3_memories_promotion_lineage
Create Date: 2026-10-01

Expand half of the step4 deployment-registry migration: create the two new
tables with their full constraint set. Backfill of builtin deployments and
governance-pending audit rows lives in the follow-up migrate/contract
revision; the tables start empty, so constraints can be complete from the
start (the plan's expand->migrate->contract relaxation applies to alters of
populated tables, not to fresh ones).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c3a91b4e2f5"
down_revision: Union[str, None] = "b3_memories_promotion_lineage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_principals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "lifecycle",
            sa.Enum("ACTIVE", "SUSPENDED", "REVOKED", name="principal_lifecycle"),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column("identity_provider", sa.String(length=255), nullable=True),
        sa.Column("idp_subject", sa.String(length=500), nullable=True),
        sa.Column("registered_by", sa.Uuid(), nullable=True),
        sa.Column("extra_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", name="uq_agent_principals_agent_live"),
    )
    op.create_index(op.f("ix_agent_principals_agent_id"), "agent_principals", ["agent_id"], unique=False)
    op.create_index(op.f("ix_agent_principals_workspace_id"), "agent_principals", ["workspace_id"], unique=False)

    op.create_table(
        "agent_deployments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("agent_version_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column(
            "backend_type",
            sa.Enum("BUILTIN", "SELF_HOSTED", "HOSTED", name="deployment_backend_type"),
            nullable=False,
        ),
        sa.Column("backend_version", sa.String(length=128), nullable=True),
        sa.Column(
            "access_mode",
            sa.Enum("IN_PROCESS", "LOCAL_PROCESS", "REMOTE_SERVICE", name="deployment_access_mode"),
            nullable=False,
        ),
        sa.Column("transport_contract_version", sa.String(length=64), nullable=True),
        sa.Column("issuer_domain", sa.String(length=255), nullable=False),
        sa.Column("endpoint", sa.String(length=1024), nullable=True),
        sa.Column("config_ref", sa.String(length=512), nullable=True),
        sa.Column("credential_ref", sa.String(length=512), nullable=True),
        sa.Column("capability_snapshot", sa.JSON(), nullable=False),
        sa.Column("axes_harness", sa.String(length=32), nullable=False),
        sa.Column("axes_environment", sa.String(length=32), nullable=False),
        sa.Column("axes_tool_execution", sa.String(length=32), nullable=False),
        sa.Column(
            "access_level",
            sa.Enum("UNVERIFIED", "COOPERATIVE", "ENFORCED", name="deployment_access_level"),
            nullable=False,
            server_default="UNVERIFIED",
        ),
        sa.Column(
            "health",
            sa.Enum("UNKNOWN", "HEALTHY", "DEGRADED", "UNREACHABLE", name="deployment_health_state"),
            nullable=False,
            server_default="UNKNOWN",
        ),
        sa.Column("implementation_language", sa.String(length=64), nullable=True),
        sa.Column("hosted_config", sa.JSON(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("registered_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_version_id"], ["agent_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # One builtin deployment per (agent, version) — backfill idempotency.
    # Self-hosted/hosted rows of the same version are legitimately distinct
    # (different endpoints/regions); dedup for those lives in the registry.
    op.create_index(
        "uq_agent_deployments_builtin_version",
        "agent_deployments",
        ["agent_id", "agent_version_id"],
        unique=True,
        postgresql_where=sa.text("backend_type = 'BUILTIN'"),
        sqlite_where=sa.text("backend_type = 'BUILTIN'"),
    )
    op.create_index("ix_agent_deployments_agent_id", "agent_deployments", ["agent_id"], unique=False)
    op.create_index("ix_agent_deployments_version_id", "agent_deployments", ["agent_version_id"], unique=False)
    op.create_index(op.f("ix_agent_deployments_workspace_id"), "agent_deployments", ["workspace_id"], unique=False)


def downgrade() -> None:
    raise RuntimeError("Step4 records must be retained; roll back the application without downgrading schema")
