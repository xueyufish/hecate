"""Exercise Step4 migrations and locking in a dedicated PostgreSQL test database.

Set HECATE_STEP4_TEST_DATABASE_URL to a postgresql+asyncpg/psycopg URL whose database
name starts with hecate_step4_review. This script uses and removes a unique
schema in that database; it refuses any other database name. --full-chain also
upgrades the dedicated test database's public schema and checks ORM column drift.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from hecate.contracts.execution.capabilities import BackendCapabilities
from hecate.contracts.execution.identity import IdentityChain, WorkloadIdentity
from hecate.contracts.execution.references import deployment_ref, run_ref, session_ref
from hecate.execution.principal_registry import PrincipalRegistry
from hecate.execution.task_run_registry import TaskRunRegistry
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AgentDeploymentModel
from hecate.models.agent_version import AgentVersionModel
from hecate.models.audit import AuditLogModel
from hecate.models.base import BaseModel
from hecate.models.conversation import ConversationModel
from hecate.models.organization import OrganizationModel
from hecate.models.run import RunModel
from hecate.models.session import SessionModel
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = (
    "7c3a91b4e2f5_add_agent_principals_and_deployments.py",
    "8e4f2a6c9d17_backfill_builtin_deployments.py",
    "c1d2e3f4a5b6_add_task_run_tables.py",
    "e9a4b72c6d10_harden_step4_registry_bindings.py",
)


def load_migration(name):
    """Load an immutable migration file without invoking the whole migration chain."""
    spec = importlib.util.spec_from_file_location(name.split("_")[0], ROOT / "alembic" / "versions" / name)
    if spec is None or spec.loader is None:
        raise RuntimeError("migration cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migrate(connection, name):
    """Execute the real upgrade against a synchronous SQLAlchemy connection."""
    with Operations.context(MigrationContext.configure(connection)):
        load_migration(name).upgrade()


async def main():
    """Verify migration preservation, semantic uniqueness and concurrent retry allocation."""
    url = make_url(os.environ["HECATE_STEP4_TEST_DATABASE_URL"])
    if url.drivername not in {"postgresql+asyncpg", "postgresql+psycopg"} or not (url.database or "").startswith(
        "hecate_step4_review"
    ):
        raise RuntimeError("only a dedicated hecate_step4_review PostgreSQL database is allowed")
    schema = "step4_review_" + uuid.uuid4().hex
    connect_args = (
        {"server_settings": {"search_path": schema}}
        if url.drivername == "postgresql+asyncpg"
        else {"options": f"-csearch_path={schema}"}
    )
    engine = create_async_engine(url, connect_args=connect_args)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    base_tables = [
        model.__table__
        for model in (
            OrganizationModel,
            UserModel,
            WorkspaceModel,
            WorkspaceMemberModel,
            AgentModel,
            AgentVersionModel,
            AuditLogModel,
            ConversationModel,
            SessionModel,
        )
    ]
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.run_sync(lambda sync: BaseModel.metadata.create_all(sync, tables=base_tables))
            # Metadata-level enum listeners also create types for excluded tables.
            # Remove only the unused Step4 types to reproduce the pre-Step4 schema.
            for name in (
                "principal_lifecycle",
                "deployment_backend_type",
                "deployment_access_mode",
                "deployment_access_level",
                "deployment_health_state",
                "enrollment_admission_result",
            ):
                await conn.execute(text(f"DROP TYPE IF EXISTS {name}"))
        async with sessions() as session:
            user = UserModel(email="migration@example.com", hashed_password=uuid.uuid4().hex)
            session.add(user)
            await session.flush()
            org = OrganizationModel(name="review", slug="review", owner_id=user.id)
            session.add(org)
            await session.flush()
            workspace = WorkspaceModel(org_id=org.id, name="review", slug="review")
            session.add(workspace)
            await session.flush()
            agent = AgentModel(workspace_id=workspace.id, name="review", published_version=99)
            orphan = AgentModel(workspace_id=workspace.id, name="no-version")
            session.add_all([agent, orphan])
            await session.flush()
            version = AgentVersionModel(
                agent_id=agent.id, version=1, config_snapshot={"model": "stub"}, content_hash="a" * 64
            )
            conversation = ConversationModel(agent_id=agent.id, workspace_id=workspace.id)
            session.add_all([version, conversation])
            await session.flush()
            legacy = SessionModel(agent_id=agent.id, conversation_id=conversation.id, workspace_id=workspace.id)
            session.add(legacy)
            await session.commit()
        async with engine.begin() as conn:
            for name in MIGRATIONS[:3]:
                await conn.run_sync(lambda sync, name=name: migrate(sync, name))
            deployment_id = (
                await conn.execute(text("SELECT id FROM agent_deployments WHERE agent_id=:agent"), {"agent": agent.id})
            ).scalar_one()
            task_id, old_run_id = uuid.uuid4(), uuid.uuid4()
            await conn.execute(
                text("""
                INSERT INTO tasks (id, goal, initiator_ref, acceptance, workspace_id, issuer_domain)
                VALUES (:id, 'legacy', '{}', '{}', :workspace, 'hecate')
            """),
                {"id": task_id, "workspace": workspace.id},
            )
            await conn.execute(
                text("""
                INSERT INTO runs (id,task_id,deployment_id,attempt_no,workspace_id,issuer_domain,
                    identity_chain,backend_ref,event_cursor,projection,origin)
                VALUES (:id,:task,:deployment,1,:workspace,'hecate','{}','{}',0,'{}','platform')
            """),
                {"id": old_run_id, "task": task_id, "deployment": deployment_id, "workspace": workspace.id},
            )
            await conn.execute(
                text("""
                INSERT INTO conversation_task_links (id,conversation_id,task_id,workspace_id,governance_status)
                VALUES (:id,:session,:task,:workspace,'ok')
            """),
                {"id": uuid.uuid4(), "session": legacy.id, "task": task_id, "workspace": workspace.id},
            )
            enrollment_id = uuid.uuid4()
            await conn.execute(
                text("""
                INSERT INTO standalone_enrollments (id,host_identity_ref,trust_root_ref,installed_versions,
                    workspace_id,managed_new_runs,admission)
                VALUES (:id,'{"issuer_domain":"corp","id":"host"}',
                    '{"issuer_domain":"corp","id":"root"}','{}',:workspace,TRUE,'ADMITTED')
            """),
                {"id": enrollment_id, "workspace": workspace.id},
            )
            await conn.execute(
                text("""
                UPDATE agent_deployments SET access_level='ENFORCED', capability_snapshot=jsonb_set(
                    capability_snapshot::jsonb,'{capabilities,provide_input}','"enforced"'::jsonb
                )::json WHERE id=:id
            """),
                {"id": deployment_id},
            )
            await conn.run_sync(lambda sync: migrate(sync, MIGRATIONS[3]))
            assert (
                await conn.execute(
                    text("SELECT managed_new_runs FROM standalone_enrollments WHERE id=:id"), {"id": enrollment_id}
                )
            ).scalar_one() is False
            assert (
                await conn.execute(
                    text("SELECT admission FROM standalone_enrollments WHERE id=:id"), {"id": enrollment_id}
                )
            ).scalar_one() == "PENDING"
            assert (
                await conn.execute(text("SELECT COUNT(*) FROM audit_logs WHERE action LIKE '%REVALIDATION_REQUIRED'"))
            ).scalar_one() == 2
            await conn.run_sync(lambda sync: migrate(sync, MIGRATIONS[1]))
            audit_count = (
                await conn.execute(
                    text("SELECT COUNT(*) FROM audit_logs WHERE action='AGENT_DEPLOYMENT_BACKFILL_PENDING'")
                )
            ).scalar_one()
            assert audit_count == 2, "backfill must be resource-scoped and idempotent"
            assert (
                await conn.execute(text("SELECT conversation_id FROM conversation_task_links"))
            ).scalar_one() == conversation.id
            assert (
                await conn.execute(text("SELECT execution_snapshot FROM runs WHERE id=:id"), {"id": old_run_id})
            ).scalar_one() is None
        async with sessions() as session:
            deployment = await session.get(AgentDeploymentModel, deployment_id)
            capabilities = BackendCapabilities.from_dict(deployment.capability_snapshot)
            assert capabilities.capabilities.provide_input.value == "unsupported"
            assert deployment.capability_snapshot["prior_capability_claim"]["provide_input"] == "enforced"
            principal = await PrincipalRegistry(session).register(agent.id, org.id, user.id)
            chain = IdentityChain(
                initiator=str(user.id),
                principal_id=str(principal.id),
                audience="enterprise-tools",
                workload=WorkloadIdentity(deployment_ref("hecate", str(deployment_id)), "review-workload"),
            )
            task = await TaskRunRegistry(session).create_task(
                goal="concurrent", initiator_ref=chain.to_dict(), workspace_id=workspace.id
            )
            await session.commit()

        async def attempt(number):
            async with sessions() as session:
                run = await TaskRunRegistry(session).create_run(
                    task_id=task.id,
                    workspace_id=workspace.id,
                    deployment_id=deployment_id,
                    identity_chain=chain,
                    backend_run_ref=run_ref("backend", f"attempt-{number}"),
                )
                await session.commit()
                return run.id, run.attempt_no

        attempts = await asyncio.gather(attempt(1), attempt(2))
        assert sorted(number for _, number in attempts) == [1, 2]
        async with sessions() as session:
            registry = TaskRunRegistry(session)
            await registry.bind_backend_session(
                attempts[0][0], workspace.id, session_ref=session_ref("vendor", "session")
            )
            other = await session.get(RunModel, attempts[1][0])
            try:
                async with session.begin_nested():
                    other.backend_session = {"id": "session", "issuer_domain": "vendor", "kind": "turn"}
                    other.backend_session_issuer, other.backend_session_id = "vendor", "session"
                    await session.flush()
            except IntegrityError:
                pass
            else:
                raise AssertionError("PostgreSQL did not reject duplicate semantic session binding")
            await session.commit()
        for name in MIGRATIONS:
            try:
                load_migration(name).downgrade()
            except RuntimeError as exc:
                assert "retained" in str(exc)
            else:
                raise AssertionError("destructive downgrade was allowed")
        async with sessions() as session:
            assert len((await session.execute(select(RunModel.id))).all()) == 3
        print(
            "PASS: PostgreSQL Step4 upgrades, idempotent backfill, real conversation mapping, "
            "concurrent retries, session uniqueness and downgrade preservation"
        )
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


def verify_full_chain():
    """Upgrade only the dedicated test database and compare production columns."""
    url = make_url(os.environ["HECATE_STEP4_TEST_DATABASE_URL"])
    if url.drivername not in {"postgresql+asyncpg", "postgresql+psycopg"} or not (url.database or "").startswith(
        "hecate_step4_review"
    ):
        raise RuntimeError("only a dedicated hecate_step4_review PostgreSQL database is allowed")
    import hecate.main  # noqa: F401

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    previous = os.environ.get("DATABASE_URL")
    try:
        os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous

    async def check():
        engine = create_async_engine(url)
        try:

            def compare(conn):
                inspector = inspect(conn)
                actual = {
                    name: {column["name"] for column in inspector.get_columns(name)}
                    for name in inspector.get_table_names()
                }
                for table in BaseModel.metadata.sorted_tables:
                    assert table.name in actual, f"missing migration table {table.name}"
                    assert {column.name for column in table.columns} <= actual[table.name], table.name

            async with engine.connect() as conn:
                await conn.run_sync(compare)
        finally:
            await engine.dispose()

    asyncio.run(check())
    print("PASS: full Alembic chain to head and production ORM column drift")


if __name__ == "__main__":
    if os.name == "nt" and make_url(os.environ["HECATE_STEP4_TEST_DATABASE_URL"]).drivername == "postgresql+psycopg":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-chain", action="store_true")
    if parser.parse_args().full_chain:
        verify_full_chain()
    asyncio.run(main())
