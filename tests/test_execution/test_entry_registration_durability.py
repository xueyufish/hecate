"""Entry registration durability (step5d entry contract completion).

With a registry session factory wired, Task/Run registration and terminal
projections commit on their own connection: a caller's request-transaction
rollback — the failure that used to erase the record of an execution that
actually happened — leaves the registration, the failed projection, and the
event envelopes queryable, while the caller's own uncommitted business
writes stay uncommitted. The fix must not commit the caller's transaction.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from hecate.contracts.execution.events import EventKind
from hecate.contracts.execution.references import run_ref as make_run_ref
from hecate.contracts.execution.references import task_ref as make_task_ref
from hecate.execution.entry_events import RunEventMapper
from hecate.execution.entry_service import CorrelationInput, EntryExecutionService
from hecate.models.agent import AgentModel
from hecate.runtime.eventstore import InMemoryEventStore
from tests.conftest import Base


@pytest.fixture
async def durability_engines(tmp_path):
    """A file-backed SQLite in WAL mode: caller and registry connections
    can hold open transactions concurrently without locking each other out."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'durability.db'}")

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _wal(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _seed_agent(factory, workspace_id: uuid.UUID) -> uuid.UUID:
    from tests.test_channel.test_chat_engine_g3_entry import _agent_with_tool

    async with factory() as session:
        agent = await _agent_with_tool(session, workspace_id, with_deployment=True)
        await session.commit()
        return agent.id


async def test_registration_and_failure_survive_caller_rollback(durability_engines, monkeypatch, default_workspace):
    from tests.test_execution.test_entry_contract_closure import _FakeWorkflowService

    monkeypatch.setattr("hecate.studio.workflows.execution_service.WorkflowExecutionService", _FakeWorkflowService)
    factory = durability_engines
    await _seed_workspace_parents(factory)
    agent_id = await _seed_agent(factory, default_workspace.id)

    boom = RuntimeError("engine exploded mid-run")
    boom.engine_events = [{"type": "message", "content": "partial"}]  # type: ignore[attr-defined]
    _FakeWorkflowService.script["behavior"] = boom

    store = InMemoryEventStore()
    async with factory() as caller:
        entry = EntryExecutionService(
            port=None,
            entry_name="rollback-test",
            db=caller,
            event_store=store,
            registry_session_factory=factory,
        )
        with pytest.raises(RuntimeError):
            await entry.execute(
                agent_mode="chat",
                messages=[{"role": "user", "content": "hi"}],
                stream=False,
                correlation=CorrelationInput(workspace_id=default_workspace.id, agent_id=agent_id),
            )
        correlation = boom.platform_correlation  # type: ignore[attr-defined]

        # The caller's own later business write stays inside its transaction.
        caller.add(AgentModel(workspace_id=default_workspace.id, name="ghost-agent"))
        await caller.flush()
        await caller.rollback()

    # A fresh session (new request) still sees the execution facts.
    async with factory() as fresh:
        from hecate.execution.task_run_registry import TaskRunRegistry

        run = await TaskRunRegistry(fresh).get_run(uuid.UUID(correlation["run_ref"]["id"]), default_workspace.id)
        assert run.projection["state"] == "failed"
        assert run.projection["error_category"] == "execution_failed"
        ghosts = (await fresh.execute(sa.select(AgentModel).where(AgentModel.name == "ghost-agent"))).scalars()
        assert list(ghosts) == []  # caller business write was not committed

    reader = RunEventMapper(
        make_task_ref(correlation["task_ref"]["issuer_domain"], correlation["task_ref"]["id"]),
        make_run_ref(correlation["run_ref"]["issuer_domain"], correlation["run_ref"]["id"]),
        event_store=store,
    )
    page = await reader.read_events()
    assert [e.kind for e in page.events] == [EventKind.EVENT, EventKind.TERMINAL]
    assert page.events[-1].payload["state"] == "failed"


async def _seed_workspace_parents(factory) -> None:
    """default_workspace rows exist in the shared test DB, not here — mirror them."""
    from hecate.models.organization import OrganizationModel
    from hecate.models.user import UserModel
    from hecate.models.workspace import WorkspaceModel
    from tests.conftest import DEFAULT_WORKSPACE_ID

    async with factory() as session:
        exists = (
            await session.execute(sa.select(WorkspaceModel).where(WorkspaceModel.id == DEFAULT_WORKSPACE_ID))
        ).scalar_one_or_none()
        if exists is None:
            org = OrganizationModel(
                id=uuid.UUID("00000000-0000-0000-0000-0000000000aa"),
                name="dur-org",
                slug="dur-org",
                owner_id=uuid.UUID("00000000-0000-0000-0000-0000000000bb"),
            )
            owner = UserModel(
                id=uuid.UUID("00000000-0000-0000-0000-0000000000bb"),
                email="dur@example.com",
                hashed_password="x",
            )
            session.add_all([owner, org])
            await session.flush()
            session.add(WorkspaceModel(id=DEFAULT_WORKSPACE_ID, org_id=org.id, name="dur-ws", slug="dur-ws"))
            await session.commit()


async def test_registry_failure_reports_missing_with_factory_wired(durability_engines, monkeypatch, default_workspace):
    """Fail-open correlation keeps its explicit missing marker on the
    independent-session path too — no fabricated correlation."""
    from tests.test_execution.test_entry_contract_closure import _FakeWorkflowService

    monkeypatch.setattr("hecate.studio.workflows.execution_service.WorkflowExecutionService", _FakeWorkflowService)
    factory = durability_engines
    await _seed_workspace_parents(factory)
    agent_id = await _seed_agent(factory, default_workspace.id)
    _FakeWorkflowService.script["behavior"] = {"content": "ok", "finish_reason": "stop"}

    async def _reject(*args, **kwargs):
        from hecate.execution.task_run_registry import TaskRunValidationError

        raise TaskRunValidationError("registry closed for registration")

    monkeypatch.setattr("hecate.execution.task_run_registry.TaskRunRegistry.create_task", _reject)

    async with factory() as caller:
        entry = EntryExecutionService(
            port=None,
            entry_name="fail-open-test",
            db=caller,
            event_store=InMemoryEventStore(),
            registry_session_factory=factory,
        )
        outcome = await entry.execute(
            agent_mode="chat",
            messages=[{"role": "user", "content": "hi"}],
            stream=False,
            correlation=CorrelationInput(workspace_id=default_workspace.id, agent_id=agent_id),
        )

    assert outcome.correlation.status == "missing"
    assert outcome.correlation.reason == "correlation registry rejected registration"
    assert outcome.result["content"] == "ok"  # execution itself was not taken down
