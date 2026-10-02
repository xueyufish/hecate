"""Harden Step4 registry bindings without fabricating historical execution facts.

Revision ID: e9a4b72c6d10
Revises: c1d2e3f4a5b6
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "e9a4b72c6d10"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for label, query in (
        (
            "deployment ownership",
            """
            SELECT d.id FROM agent_deployments d JOIN agents a ON a.id = d.agent_id
            JOIN agent_versions v ON v.id = d.agent_version_id
            WHERE d.workspace_id <> a.workspace_id OR v.agent_id <> d.agent_id LIMIT 1
        """,
        ),
        (
            "principal ownership",
            """
            SELECT p.id FROM agent_principals p JOIN agents a ON a.id = p.agent_id
            JOIN workspaces w ON w.id = a.workspace_id
            WHERE p.workspace_id <> a.workspace_id OR p.organization_id <> w.org_id LIMIT 1
        """,
        ),
        (
            "task/run/enrollment workspace",
            """
            SELECT t.id FROM tasks t LEFT JOIN workspaces w ON w.id = t.workspace_id WHERE w.id IS NULL
            UNION ALL
            SELECT e.id FROM standalone_enrollments e LEFT JOIN workspaces w ON w.id = e.workspace_id
            WHERE w.id IS NULL
            UNION ALL
            SELECT r.id FROM runs r JOIN tasks t ON t.id = r.task_id
            JOIN agent_deployments d ON d.id = r.deployment_id
            WHERE r.workspace_id <> t.workspace_id OR r.workspace_id <> d.workspace_id LIMIT 1
            """,
        ),
        (
            "backend session reference",
            """
            SELECT id FROM runs WHERE backend_session IS NOT NULL AND json_typeof(backend_session) <> 'null'
                AND (json_typeof(backend_session -> 'id') IS DISTINCT FROM 'string'
                    OR json_typeof(backend_session -> 'issuer_domain') IS DISTINCT FROM 'string'
                    OR btrim(backend_session ->> 'id') = '' OR btrim(backend_session ->> 'issuer_domain') = '')
            LIMIT 1
        """,
        ),
        (
            "local source reference",
            """
            SELECT id FROM runs WHERE local_source IS NOT NULL AND json_typeof(local_source) <> 'null'
                AND (json_typeof(local_source -> 'local_run_id') IS DISTINCT FROM 'string'
                    OR json_typeof(local_source -> 'issuer_domain') IS DISTINCT FROM 'string'
                    OR btrim(local_source ->> 'local_run_id') = '' OR btrim(local_source ->> 'issuer_domain') = '')
            LIMIT 1
        """,
        ),
    ):
        if bind.execute(sa.text(query)).first() is not None:
            raise RuntimeError(f"Step4 invalid {label}; remediate before retrying migration")
    for name, size in (
        ("backend_session_issuer", 255),
        ("backend_session_id", 512),
        ("local_source_issuer", 255),
        ("local_run_id", 512),
        ("control_owner", 32),
    ):
        op.add_column("runs", sa.Column(name, sa.String(size), nullable=True))
    op.add_column("runs", sa.Column("execution_snapshot", sa.JSON(), nullable=True))

    # Match semantic identity, independent of JSON whitespace, key order or kind.
    op.drop_index("uq_runs_backend_session", table_name="runs")
    bind.execute(
        sa.text("""
        UPDATE runs SET backend_session_issuer = backend_session ->> 'issuer_domain',
                        backend_session_id = backend_session ->> 'id',
                        local_source_issuer = local_source ->> 'issuer_domain',
                        local_run_id = local_source ->> 'local_run_id',
                        control_owner = CASE WHEN origin = 'imported_observation' THEN 'host' ELSE 'platform' END
    """)
    )
    # Creating these indexes deliberately aborts on ambiguous existing bindings.
    op.create_index(
        "uq_runs_backend_session",
        "runs",
        ["backend_session_issuer", "backend_session_id"],
        unique=True,
        postgresql_where=sa.text("backend_session_id IS NOT NULL"),
    )
    op.create_index(
        "uq_runs_local_source",
        "runs",
        ["workspace_id", "deployment_id", "local_source_issuer", "local_run_id"],
        unique=True,
        postgresql_where=sa.text("local_run_id IS NOT NULL"),
    )
    op.create_index(
        "uq_agent_deployments_default",
        "agent_deployments",
        ["agent_id"],
        unique=True,
        postgresql_where=sa.text("is_default AND NOT deleted"),
    )

    inspector = sa.inspect(bind)
    for fk in inspector.get_foreign_keys("conversation_task_links"):
        if fk["constrained_columns"] == ["conversation_id"] and fk["referred_table"] == "sessions":
            invalid = bind.execute(
                sa.text("""
                    SELECT l.id FROM conversation_task_links l
                    LEFT JOIN sessions s ON s.id = l.conversation_id
                    LEFT JOIN conversations c ON c.id = s.conversation_id
                    WHERE c.id IS NULL OR c.workspace_id <> l.workspace_id OR s.workspace_id <> l.workspace_id
                    LIMIT 1
                """)
            ).first()
            if invalid is not None:
                raise RuntimeError(
                    "Step4 legacy session link cannot resolve to a same-workspace conversation; remediate before retry"
                )
            op.drop_constraint(fk["name"], "conversation_task_links", type_="foreignkey")
            bind.execute(
                sa.text("""
                    UPDATE conversation_task_links l SET conversation_id = s.conversation_id,
                        governance_status = 'pending'
                    FROM sessions s WHERE s.id = l.conversation_id
                """)
            )
            op.create_foreign_key(
                "fk_conversation_task_links_conversation",
                "conversation_task_links",
                "conversations",
                ["conversation_id"],
                ["id"],
                ondelete="RESTRICT",
            )
    for fk in inspector.get_foreign_keys("runs"):
        if fk["constrained_columns"] == ["task_id"]:
            op.drop_constraint(fk["name"], "runs", type_="foreignkey")
            op.create_foreign_key("fk_runs_task", "runs", "tasks", ["task_id"], ["id"], ondelete="RESTRICT")

    # Prior registration only checked names, not trust; it cannot grant admission.
    bind.execute(
        sa.text("""
        WITH changed AS (
            UPDATE standalone_enrollments SET managed_new_runs = FALSE, admission = 'PENDING'
            WHERE managed_new_runs OR admission = 'ADMITTED' RETURNING id, workspace_id
        )
        INSERT INTO audit_logs (id,org_id,workspace_id,user_id,action,resource_type,resource_id,
                                success,metadata,created_at,updated_at,deleted)
        SELECT gen_random_uuid(),w.org_id,c.workspace_id,'00000000-0000-0000-0000-000000000000',
            'STANDALONE_ENROLLMENT_REVALIDATION_REQUIRED','standalone_enrollment',c.id,TRUE,
            '{"reason":"prior admission only validated names; trusted revalidation required"}',
            CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,FALSE
        FROM changed c JOIN workspaces w ON w.id=c.workspace_id
    """)
    )
    op.create_check_constraint(
        "ck_enrollment_managed_admitted",
        "standalone_enrollments",
        "NOT managed_new_runs OR admission = 'ADMITTED'",
    )
    # Historical snapshots and audiences stay NULL/missing; current configuration
    # cannot prove what a previously created run actually used.
    bind.execute(
        sa.text("""
        UPDATE agent_deployments SET capability_snapshot = jsonb_set(
            capability_snapshot::jsonb, '{capabilities}',
            '{"provide_input":"unsupported","resolve_approval":"unsupported",
              "pause":"unsupported","resume":"unsupported","export_context":"unsupported"}'::jsonb
        )::json WHERE capability_snapshot -> 'capabilities' IS NULL
            OR json_typeof(capability_snapshot -> 'capabilities') = 'null'
    """)
    )
    bind.execute(
        sa.text("""
        WITH changed AS (
            UPDATE agent_deployments SET access_level = 'UNVERIFIED'
            WHERE access_level <> 'UNVERIFIED' RETURNING id, workspace_id
        )
        INSERT INTO audit_logs (id,org_id,workspace_id,user_id,action,resource_type,resource_id,
                                success,metadata,created_at,updated_at,deleted)
        SELECT gen_random_uuid(),w.org_id,c.workspace_id,'00000000-0000-0000-0000-000000000000',
            'AGENT_DEPLOYMENT_REVALIDATION_REQUIRED','agent_deployment',c.id,TRUE,
            '{"reason":"prior registration did not certify admission grade"}',
            CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,FALSE
        FROM changed c JOIN workspaces w ON w.id=c.workspace_id
    """)
    )
    bind.execute(
        sa.text("""
        UPDATE agent_deployments d SET capability_snapshot = (
            d.capability_snapshot::jsonb || jsonb_build_object(
                'prior_capability_claim', d.capability_snapshot::jsonb -> 'capabilities',
                'capabilities', (SELECT jsonb_object_agg(c.key,
                    CASE WHEN c.value = '"enforced"'::jsonb THEN '"unsupported"'::jsonb ELSE c.value END)
                    FROM jsonb_each(d.capability_snapshot::jsonb -> 'capabilities') c)
            )
        )::json WHERE jsonb_typeof(d.capability_snapshot::jsonb -> 'capabilities') = 'object'
            AND EXISTS (SELECT 1 FROM jsonb_each(d.capability_snapshot::jsonb -> 'capabilities') c
                        WHERE c.value = '"enforced"'::jsonb)
    """)
    )
    bind.execute(
        sa.text("""
        UPDATE agent_deployments SET hosted_config = jsonb_set(
            COALESCE(hosted_config::jsonb, '{}'::jsonb)
                || jsonb_build_object('prior_verification_claim', hosted_config -> 'verification'),
            '{verification}', '"unverified"'::jsonb
        )::json WHERE backend_type = 'HOSTED'
    """)
    )
    bind.execute(
        sa.text("""
        UPDATE audit_logs a SET org_id = w.org_id FROM workspaces w
        WHERE a.workspace_id = w.id AND a.action IN (
            'AGENT_PRINCIPAL_REGISTERED', 'AGENT_PRINCIPAL_TRANSITION', 'AGENT_DEPLOYMENT_REGISTERED',
            'AGENT_DEPLOYMENT_DEFAULT_SET', 'AGENT_DEPLOYMENT_BACKFILL_PENDING',
            'STANDALONE_ENROLLMENT_REGISTERED', 'STANDALONE_ENROLLMENT_ADMITTED', 'STANDALONE_ENROLLMENT_REJECTED'
        )
    """)
    )
    # Reuse the repaired, idempotent builtin backfill for already-upgraded sites.
    path = Path(__file__).with_name("8e4f2a6c9d17_backfill_builtin_deployments.py")
    spec = importlib.util.spec_from_file_location("step4_backfill", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Step4 backfill migration is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.upgrade()


def downgrade() -> None:
    raise RuntimeError("Step4 records must be retained; roll back the application without downgrading schema")
