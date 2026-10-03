"""Unit tests for the platform entry-point execution service (step5d).

Covers the ``platform-entry-execution`` spec: one entry service owning
assembly + correlation, per-request and shared-task correlation modes,
fail-open correlation with explicit missing markers, and the absence of
engine-specific imports in the entry module itself.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.contracts.execution.references import RefKind
from hecate.execution.entry_service import (
    CORRELATION_MISSING,
    CorrelationInput,
    EntryExecutionService,
)
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.agent_version import AgentVersionModel
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel

ZERO_WS = uuid.UUID("00000000-0000-0000-0000-000000000000")


class StubExecService:
    """Records constructor wiring and execute() calls."""

    instances: list[StubExecService] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.calls: list[dict] = []
        StubExecService.instances.append(self)

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):

            async def _gen():
                yield {"type": "message", "content": "hi"}

            return _gen()
        return {"content": "hi", "finish_reason": "stop"}


@pytest.fixture(autouse=True)
def _stub_exec(monkeypatch):
    """Patch the lazy sibling-domain import target for every test."""
    import hecate.studio.workflows.execution_service as exec_module

    StubExecService.instances.clear()
    monkeypatch.setattr(exec_module, "WorkflowExecutionService", StubExecService)


@pytest.fixture
def workspace_id(default_workspace: WorkspaceModel) -> uuid.UUID:
    return default_workspace.id


async def _make_agent_with_deployment(
    db_session: AsyncSession, workspace_id: uuid.UUID, *, with_deployment: bool = True
) -> AgentModel:
    agent = AgentModel(workspace_id=workspace_id, name=f"agent-{uuid.uuid4().hex[:8]}")
    db_session.add(agent)
    await db_session.flush()
    if with_deployment:
        version = AgentVersionModel(
            agent_id=agent.id,
            version=1,
            config_snapshot={"model": "stub"},
            content_hash="a" * 64,
        )
        db_session.add(version)
        await db_session.flush()
        user = UserModel(email=f"owner-{agent.id}@example.com", hashed_password=uuid.uuid4().hex)
        db_session.add(user)
        await db_session.flush()
        db_session.add(
            AgentPrincipalModel(
                agent_id=agent.id,
                workspace_id=workspace_id,
                organization_id=workspace_id,
                owner_user_id=user.id,
            )
        )
        await db_session.flush()
        db_session.add(
            AgentDeploymentModel(
                agent_id=agent.id,
                agent_version_id=version.id,
                workspace_id=workspace_id,
                backend_type=BackendType.BUILTIN,
                access_mode=AccessMode.IN_PROCESS,
                issuer_domain="hecate",
                is_default=True,
                capability_snapshot={},
                axes_harness="hecate",
                axes_environment="none",
                axes_tool_execution="hecate_gateway",
            )
        )
        await db_session.flush()
    return agent


def _service(workspace_id: uuid.UUID, db_session: AsyncSession) -> EntryExecutionService:
    return EntryExecutionService(port=object(), entry_name="http-chat", db=db_session)


# --- delegation and wiring ------------------------------------------------


async def test_entry_service_owns_assembly_and_forwards_wiring(db_session, workspace_id) -> None:
    """Entries hand in wiring; the service constructs the platform adapter."""
    service = _service(workspace_id, db_session)
    assert isinstance(service._service, StubExecService)
    # Guardrail wiring must be forwarded so entries cannot bypass the bundle.
    assert "port" in service._service.kwargs


async def test_execute_delegates_and_returns_engine_result(db_session, workspace_id) -> None:
    agent = await _make_agent_with_deployment(db_session, workspace_id, with_deployment=False)
    service = _service(workspace_id, db_session)
    outcome = await service.execute(
        agent_mode="chat",
        messages=[{"role": "user", "content": "hello"}],
        agent_id=str(agent.id),
        correlation=CorrelationInput(workspace_id=workspace_id, agent_id=agent.id, goal="say hi"),
    )
    assert outcome.result == {"content": "hi", "finish_reason": "stop"}
    assert len(service._service.calls) == 1
    # Session id minted when absent so correlation points at a real session.
    assert service._service.calls[0]["session_id"] is not None


async def test_streaming_result_is_engine_generator(db_session, workspace_id) -> None:
    agent = await _make_agent_with_deployment(db_session, workspace_id, with_deployment=False)
    service = _service(workspace_id, db_session)
    outcome = await service.execute(
        agent_mode="chat",
        stream=True,
        messages=[{"role": "user", "content": "hello"}],
        correlation=CorrelationInput(workspace_id=workspace_id, agent_id=agent.id),
    )
    chunks = [event async for event in outcome.result]
    assert chunks == [{"type": "message", "content": "hi"}]


# --- per-request correlation -----------------------------------------------


async def test_per_request_correlation_creates_task_and_run(db_session, workspace_id) -> None:
    agent = await _make_agent_with_deployment(db_session, workspace_id)
    service = _service(workspace_id, db_session)
    session_id = uuid.uuid4()
    outcome = await service.execute(
        agent_mode="chat",
        messages=[{"role": "user", "content": "hello"}],
        correlation=CorrelationInput(
            workspace_id=workspace_id,
            agent_id=agent.id,
            user_id=uuid.uuid4(),
            session_id=session_id,
            goal="answer the question",
        ),
    )
    assert outcome.correlated
    assert outcome.correlation.task_ref is not None
    assert outcome.correlation.task_ref.kind is RefKind.TASK
    assert outcome.correlation.run_ref is not None
    assert outcome.correlation.run_ref.kind is RefKind.RUN
    # The platform run row's backend reference points at the engine session.
    from hecate.execution.task_run_registry import TaskRunRegistry

    run = await TaskRunRegistry(db_session).get_run(uuid.UUID(outcome.correlation.run_ref.id), workspace_id)
    assert run.backend_ref["id"] == str(session_id)
    from sqlalchemy import select

    principal = await db_session.scalar(select(AgentPrincipalModel).where(AgentPrincipalModel.agent_id == agent.id))
    assert principal.id != agent.id
    assert run.identity_chain["principal_id"] == str(principal.id)


async def test_per_request_correlation_without_deployment_still_records_task(db_session, workspace_id) -> None:
    """Task responsibility is recorded; the missing run is explicit."""
    agent = await _make_agent_with_deployment(db_session, workspace_id, with_deployment=False)
    service = _service(workspace_id, db_session)
    outcome = await service.execute(
        agent_mode="chat",
        messages=[{"role": "user", "content": "hello"}],
        correlation=CorrelationInput(workspace_id=workspace_id, agent_id=agent.id),
    )
    assert outcome.correlated
    assert outcome.correlation.task_ref is not None
    assert outcome.correlation.run_ref is None
    assert "no default deployment" in outcome.correlation.reason


async def test_correlation_failure_is_explicit_not_silent(db_session, workspace_id) -> None:
    """A registry failure yields the missing marker with a reason."""
    service = _service(workspace_id, db_session)
    outcome = await service.execute(
        agent_mode="chat",
        messages=[{"role": "user", "content": "hello"}],
        correlation=CorrelationInput(workspace_id=None),
    )
    assert not outcome.correlated
    assert outcome.correlation.status == CORRELATION_MISSING
    assert outcome.correlation.reason


async def test_unknown_workspace_fails_open_with_reason(db_session, workspace_id) -> None:
    unknown_ws = uuid.uuid4()
    agent = await _make_agent_with_deployment(db_session, workspace_id, with_deployment=False)
    service = _service(workspace_id, db_session)
    outcome = await service.execute(
        agent_mode="chat",
        messages=[{"role": "user", "content": "hello"}],
        correlation=CorrelationInput(workspace_id=unknown_ws, agent_id=agent.id),
    )
    assert outcome.correlation.status == CORRELATION_MISSING
    assert outcome.correlation.task_ref is None


# --- shared-task correlation (evaluation mode) ------------------------------


async def test_shared_task_correlation_attaches_to_existing_task(db_session, workspace_id) -> None:
    from hecate.execution.task_run_registry import TaskRunRegistry

    registry = TaskRunRegistry(db_session)
    chain = {
        "initiator": None,
        "principal_id": "evaluation-runner",
        "workload": {
            "deployment": {"kind": "deployment", "issuer_domain": "hecate", "id": "eval-1"},
            "workload_id": "hecate:evaluation",
        },
        "audience": "hecate:evaluation",
    }
    task = await registry.create_task(goal="run evaluation", initiator_ref=chain, workspace_id=workspace_id)

    agent = await _make_agent_with_deployment(db_session, workspace_id, with_deployment=False)
    service = EntryExecutionService(port=object(), entry_name="evaluation", db=db_session)
    outcome = await service.execute(
        agent_mode="workflow",
        workflow_id=uuid.uuid4(),
        messages=[{"role": "user", "content": "q"}],
        correlation=CorrelationInput(
            workspace_id=workspace_id,
            agent_id=agent.id,
            existing_task_id=task.id,
        ),
    )
    assert outcome.correlated
    assert outcome.correlation.task_ref.id == str(task.id)
    assert outcome.correlation.run_ref is None


async def test_shared_task_cross_workspace_is_missing(db_session, workspace_id) -> None:
    from hecate.execution.task_run_registry import TaskRunRegistry

    registry = TaskRunRegistry(db_session)
    chain = {
        "initiator": None,
        "principal_id": "evaluation-runner",
        "workload": {
            "deployment": {"kind": "deployment", "issuer_domain": "hecate", "id": "eval-1"},
            "workload_id": "hecate:evaluation",
        },
        "audience": "hecate:evaluation",
    }
    task = await registry.create_task(goal="run evaluation", initiator_ref=chain, workspace_id=workspace_id)

    other_ws = uuid.uuid4()
    db_session.add(WorkspaceModel(id=other_ws, org_id=task.workspace_id, name="other", slug="other"))
    await db_session.flush()

    service = EntryExecutionService(port=object(), entry_name="evaluation", db=db_session)
    outcome = await service.execute(
        agent_mode="workflow",
        messages=[{"role": "user", "content": "q"}],
        correlation=CorrelationInput(workspace_id=other_ws, existing_task_id=task.id),
    )
    # The shared task belongs to another workspace — correlation is explicitly
    # missing, execution is untouched.
    assert outcome.correlation.status == CORRELATION_MISSING


# --- layering ----------------------------------------------------------------


def test_entry_module_has_no_engine_specific_imports() -> None:
    """The entry service must not import engine concrete classes."""
    import ast
    from pathlib import Path

    source = Path("src/hecate/execution/entry_service.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
    joined = "\n".join(imported)
    assert "pregel" not in joined.lower()
    assert "graph_compiler" not in joined.lower()
    assert "GraphCompiler" not in source
    assert "PregelRuntime" not in source


async def test_registry_flush_failure_rolls_back_only_correlation(db_session, workspace_id, monkeypatch):
    from sqlalchemy import func, select

    from hecate.execution.task_run_registry import TaskRunRegistry
    from hecate.models.task import TaskModel

    agent = await _make_agent_with_deployment(db_session, workspace_id)
    service = _service(workspace_id, db_session)

    async def failing_run(registry, **kwargs):
        registry._session.add(
            TaskModel(goal=None, initiator_ref={}, workspace_id=workspace_id, issuer_domain="private")
        )
        await registry._session.flush()

    monkeypatch.setattr(TaskRunRegistry, "create_run", failing_run)
    outcome = await service.execute(
        messages=[{"role": "user", "content": "hello"}],
        correlation=CorrelationInput(workspace_id=workspace_id, agent_id=agent.id),
    )
    assert outcome.result["content"] == "hi"
    assert outcome.correlation.status == CORRELATION_MISSING
    assert outcome.correlation.reason == "correlation registration failed"
    assert db_session.is_active
    assert await db_session.scalar(select(func.count()).select_from(TaskModel)) == 0
    assert await db_session.get(AgentModel, agent.id) is agent


async def test_mismatched_sessions_are_rejected_before_execution(db_session, workspace_id):
    service = _service(workspace_id, db_session)
    with pytest.raises(ValueError, match="session identifiers must match"):
        await service.execute(session_id=uuid.uuid4(), correlation=CorrelationInput(session_id=uuid.uuid4()))
    assert service._service.calls == []


async def test_default_entry_stores_are_shared_even_without_tools(db_session, workspace_id):
    from hecate.core.composition.entry_assembly import get_shared_event_store, get_shared_session_state_store

    service = _service(workspace_id, db_session)
    assert service._service.kwargs["event_store"] is get_shared_event_store()
    assert service._service.kwargs["checkpoint_store"] is get_shared_session_state_store()


async def test_evaluation_threads_scope_version_and_shared_task(db_session, workspace_id):
    from hecate.execution.task_run_registry import TaskRunRegistry
    from hecate.models.workflow import WorkflowModel
    from hecate.ops.evaluation.engine import EvaluationEngine

    workflow = WorkflowModel(name="evaluation", workspace_id=workspace_id)
    db_session.add(workflow)
    await db_session.flush()
    task = await TaskRunRegistry(db_session).create_task(
        goal="evaluate",
        workspace_id=workspace_id,
        initiator_ref={
            "initiator": None,
            "principal_id": "eval",
            "audience": "evaluation",
            "workload": {
                "deployment": {"kind": "deployment", "issuer_domain": "hecate", "id": "eval"},
                "workload_id": "eval",
            },
        },
    )
    result, trajectory = await EvaluationEngine(db_session)._generate_answer_via_workflow(
        query="hello",
        workflow_id=workflow.id,
        workflow_version=2,
        item_id=uuid.uuid4(),
        repetitions=2,
        correlation_task_id=task.id,
    )
    assert result == "hi" and len(trajectory) == 2
    assert all(row["correlation"]["task_ref"]["id"] == str(task.id) for row in trajectory)
    calls = StubExecService.instances[-1].calls
    assert len(calls) == 2
    assert all(call["workflow_version"] == 2 and call["workspace_id"] == workspace_id for call in calls)
    assert "correlation" not in calls[0]


async def test_evaluation_rejects_cross_workspace_anchor(db_session, workspace_id):
    from hecate.models.task import TaskModel
    from hecate.models.workflow import WorkflowModel
    from hecate.ops.evaluation.engine import EvaluationEngine

    workflow = WorkflowModel(name="evaluation", workspace_id=workspace_id)
    task = TaskModel(goal="foreign", workspace_id=uuid.uuid4(), initiator_ref={}, issuer_domain="hecate")
    db_session.add_all([workflow, task])
    await db_session.flush()
    with pytest.raises(ValueError, match="same workspace"):
        await EvaluationEngine(db_session)._generate_answer_via_workflow(
            query="hello",
            workflow_id=workflow.id,
            workflow_version=1,
            item_id=uuid.uuid4(),
            repetitions=1,
            correlation_task_id=task.id,
        )
    assert StubExecService.instances == []
