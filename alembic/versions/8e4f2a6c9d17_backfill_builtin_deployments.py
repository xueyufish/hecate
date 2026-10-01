"""backfill builtin deployments and governance-pending audit rows

Revision ID: 8e4f2a6c9d17
Revises: 7c3a91b4e2f5
Create Date: 2026-10-01

Migrate/contract half of the step4 deployment-registry migration:

- every agent with at least one version gets a default builtin deployment
  (published version preferred, else latest). Idempotent by construction:
  ``WHERE NOT EXISTS`` plus the partial unique index
  ``uq_agent_deployments_builtin_version`` makes replays no-ops.
- agents without any version cannot reference a deployment version; they
  are recorded as governance-pending audit rows instead of being silently
  skipped or fabricating version bindings. No principal rows are created
  here: un-mappable governance data stays pending (plan step4).
- contract tightening: nothing to alter — the expand revision created the
  fresh tables with full constraints.

Rollback keeps the new tables (plan step4: "回滚应用时保留新表"): the
downgrade removes only the backfilled rows/audit entries.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8e4f2a6c9d17"
down_revision: Union[str, None] = "7c3a91b4e2f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BUILTIN_AXES = ("hecate", "none", "hecate_gateway")


def upgrade() -> None:
    bind = op.get_bind()

    bind.execute(
        sa.text(
            """
            INSERT INTO agent_deployments (
                id, agent_id, agent_version_id, workspace_id,
                backend_type, access_mode, issuer_domain,
                capability_snapshot, axes_harness, axes_environment,
                axes_tool_execution, access_level, health,
                is_default, created_at, updated_at, deleted
            )
            SELECT
                gen_random_uuid(),
                a.id,
                v.id,
                a.workspace_id,
                'BUILTIN',
                'IN_PROCESS',
                'hecate',
                '{"contract_version": "0.1", "backend_type": "builtin", "ownership": {"harness": "hecate", "environment": "none", "tool_execution": "hecate_gateway"}}',
                :harness,
                :environment,
                :tool_execution,
                'UNVERIFIED',
                'UNKNOWN',
                TRUE,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP,
                FALSE
            FROM agents a
            JOIN agent_versions v
              ON v.agent_id = a.id
             AND v.version = COALESCE(
                     a.published_version,
                     (SELECT MAX(v2.version) FROM agent_versions v2 WHERE v2.agent_id = a.id)
                 )
            WHERE a.deleted IS FALSE
              AND NOT EXISTS (
                  SELECT 1 FROM agent_deployments d
                  WHERE d.agent_id = a.id
                    AND d.agent_version_id = v.id
                    AND d.backend_type = 'BUILTIN'
              )
            """
        ),
        {"harness": _BUILTIN_AXES[0], "environment": _BUILTIN_AXES[1], "tool_execution": _BUILTIN_AXES[2]},
    )

    # Governance-pending record for agents that have no version snapshot at
    # all — visible debt, never a fabricated deployment.
    bind.execute(
        sa.text(
            """
            INSERT INTO audit_logs (
                id, org_id, workspace_id, user_id, action,
                resource_type, success, metadata, created_at
            )
            SELECT
                gen_random_uuid(),
                a.workspace_id,
                a.workspace_id,
                '00000000-0000-0000-0000-000000000000',
                'AGENT_DEPLOYMENT_BACKFILL_PENDING',
                'agent',
                TRUE,
                '{"reason": "agent has no version snapshot; builtin deployment not created"}',
                CURRENT_TIMESTAMP
            FROM agents a
            WHERE a.deleted IS FALSE
              AND NOT EXISTS (SELECT 1 FROM agent_versions v WHERE v.agent_id = a.id)
              AND NOT EXISTS (
                  SELECT 1 FROM audit_logs al
                  WHERE al.action = 'AGENT_DEPLOYMENT_BACKFILL_PENDING'
                    AND al.resource_type = 'agent'
                    AND al.resource_id = a.id
              )
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    # Keep the tables (plan step4 rollback rule); remove only backfill data.
    bind.execute(sa.text("DELETE FROM agent_deployments WHERE backend_type = 'BUILTIN'"))
    bind.execute(sa.text("DELETE FROM audit_logs WHERE action = 'AGENT_DEPLOYMENT_BACKFILL_PENDING'"))
