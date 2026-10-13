"""Tests for scheduling executors — registry, agent, workflow."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.agent import AgentModel
from hecate.models.task import TaskModel
from hecate.ops.scheduling.executors import (
    AgentExecutor,
    ExecutorRegistry,
    WorkflowExecutor,
    create_default_registry,
)


def _session_factory(session: AsyncSession):
    """Build an ``async_session_factory`` stand-in yielding ``session``.

    The executors import ``hecate.core.database.async_session_factory``
    lazily inside ``execute()``, so patching the module attribute is enough
    to route their DB access at the test session (conftest ``db_session``).
    """

    @asynccontextmanager
    async def _factory() -> AsyncIterator[AsyncSession]:
        yield session

    return _factory


class _StubLLMService:
    """Deterministic provider double at the llm_service seam."""

    def __init__(self, content: str = "scheduled reply") -> None:
        self.content = content
        self.chat_calls: list[dict] = []
        self.stream_calls: list[dict] = []

    async def chat(self, *, messages, model=None, tools=None, **_kwargs):
        self.chat_calls.append({"messages": list(messages), "model": model, "tools": tools})
        return SimpleNamespace(
            content=self.content,
            model=model or "stub-model",
            tool_calls=None,
            finish_reason="stop",
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )

    async def chat_stream(self, *, messages, model=None, tools=None, **_kwargs):
        self.stream_calls.append({"messages": list(messages), "model": model, "tools": tools})
        yield {"content": self.content, "tool_calls": None}
        yield {"content": "", "finish_reason": "stop", "tool_calls": None}


class TestExecutorRegistry:
    def test_register_and_get(self) -> None:
        registry = ExecutorRegistry()
        executor = AgentExecutor()
        registry.register("agent", executor)
        assert registry.get("agent") is executor

    def test_get_unknown_type_returns_none(self) -> None:
        registry = ExecutorRegistry()
        assert registry.get("unknown") is None

    def test_registered_types(self) -> None:
        registry = create_default_registry()
        types = registry.registered_types
        assert "agent" in types
        assert "workflow" in types

    def test_create_default_registry(self) -> None:
        registry = create_default_registry()
        assert isinstance(registry.get("agent"), AgentExecutor)
        assert isinstance(registry.get("workflow"), WorkflowExecutor)


class TestAgentExecutor:
    async def test_missing_agent_id(self) -> None:
        executor = AgentExecutor()
        result = await executor.execute(uuid.uuid4(), {})
        assert result["status"] == "failed"
        assert "agent_id" in result["error"]

    async def test_runs_agent_via_entry_service(self, db_session: AsyncSession, default_workspace) -> None:
        """The executor delegates to the platform entry service: the run
        flows through the provider seam (no direct llm_service.chat call)
        and the execution is correlated to a registered Task."""
        import hecate_llm.service

        stub = _StubLLMService()
        agent = AgentModel(
            name="Sched Agent",
            persona="You are the scheduled agent.",
            workspace_id=uuid.uuid4(),  # differs from the task row: row context must win
            model_config_db={"model": "test-model"},
        )
        db_session.add(agent)
        await db_session.flush()
        row_workspace = default_workspace.id

        with (
            patch("hecate.core.database.async_session_factory", _session_factory(db_session)),
            patch.object(hecate_llm.service, "llm_service", stub),
        ):
            result = await AgentExecutor().execute(
                uuid.uuid4(),
                {"message": "hello"},
                workspace_id=row_workspace,
                agent_id=agent.id,
            )

        assert result["status"] == "success"
        assert result["result"] == "scheduled reply"
        # Execution went through the provider seam exactly once.
        invocations = stub.chat_calls + stub.stream_calls
        assert len(invocations) == 1
        assert invocations[0]["model"] == "test-model"
        # Task registered under the task-row workspace (authoritative source).
        tasks = (
            (await db_session.execute(select(TaskModel).where(TaskModel.workspace_id == row_workspace))).scalars().all()
        )
        assert len(tasks) == 1

    async def test_task_config_agent_id_fallback(self, db_session: AsyncSession) -> None:
        """Compat: without row-context args the task_config binding still runs."""
        import hecate_llm.service

        stub = _StubLLMService()
        agent = AgentModel(name="Fallback Agent", model_config_db={"model": "test-model"})
        db_session.add(agent)
        await db_session.flush()

        with (
            patch("hecate.core.database.async_session_factory", _session_factory(db_session)),
            patch.object(hecate_llm.service, "llm_service", stub),
        ):
            result = await AgentExecutor().execute(uuid.uuid4(), {"agent_id": str(agent.id), "message": "hi"})

        assert result["status"] == "success"
        assert result["result"] == "scheduled reply"

    async def test_unknown_agent_fails(self, db_session: AsyncSession) -> None:
        with patch("hecate.core.database.async_session_factory", _session_factory(db_session)):
            result = await AgentExecutor().execute(uuid.uuid4(), {"agent_id": str(uuid.uuid4())})
        assert result["status"] == "failed"
        assert "not found" in result["error"]

    async def test_llm_failure_is_reported(self, db_session: AsyncSession) -> None:
        import hecate_llm.service

        class _Broken:
            async def chat(self, **_kwargs):
                raise RuntimeError("llm down")

            async def chat_stream(self, **_kwargs):
                raise RuntimeError("llm down")
                yield  # pragma: no cover

        agent = AgentModel(name="Broken Agent", model_config_db={"model": "test-model"})
        db_session.add(agent)
        await db_session.flush()

        with (
            patch("hecate.core.database.async_session_factory", _session_factory(db_session)),
            patch.object(hecate_llm.service, "llm_service", _Broken()),
        ):
            result = await AgentExecutor().execute(uuid.uuid4(), {"agent_id": str(agent.id), "message": "hello"})
        assert result["status"] == "failed"
        assert "llm down" in result["error"]


class TestWorkflowExecutor:
    async def test_missing_workflow_id(self) -> None:
        executor = WorkflowExecutor()
        result = await executor.execute(uuid.uuid4(), {})
        assert result["status"] == "failed"
        assert "workflow_id" in result["error"]

    async def test_missing_workflow_row_reports_failed(self, db_session: AsyncSession) -> None:
        with patch("hecate.core.database.async_session_factory", _session_factory(db_session)):
            result = await WorkflowExecutor().execute(
                uuid.uuid4(),
                {"workflow_id": str(uuid.uuid4())},
            )
        assert result["status"] == "failed"
        assert "not found" in result["error"]

    async def test_runs_workflow_through_entry_service(self, db_session: AsyncSession) -> None:
        """step5d closure: the scheduler drives the platform entry service
        (agent_mode=workflow, creator-workspace correlation) — no test
        runner on the scheduled path."""
        from hecate.contracts.execution.references import run_ref as make_run_ref
        from hecate.execution.entry_service import CorrelationResult
        from hecate.models.workflow import WorkflowModel

        workflow_id = uuid.uuid4()
        db_session.add(
            WorkflowModel(
                id=workflow_id,
                workspace_id=uuid.UUID(int=0),
                name="scheduled wf",
            )
        )
        await db_session.flush()

        captured: dict[str, Any] = {}
        correlation = CorrelationResult(
            status="registered",
            task_ref=None,
            run_ref=make_run_ref("hecate", str(uuid.uuid4())),
        )

        class _FakeEntry:
            def __init__(self, **kwargs: Any) -> None:
                captured["init"] = kwargs

            async def execute(self, **kwargs: Any):
                captured["execute"] = kwargs
                from hecate.execution.entry_service import ExecutionOutcome

                return ExecutionOutcome(
                    result={"content": "workflow output", "finish_reason": "stop"},
                    correlation=correlation,
                )

        with (
            patch("hecate.core.database.async_session_factory", _session_factory(db_session)),
            patch("hecate.core.composition.runtime_port_adapter.create_runtime_port", MagicMock(return_value=None)),
            patch("hecate.execution.entry_service.EntryExecutionService", _FakeEntry),
        ):
            result = await WorkflowExecutor().execute(
                uuid.uuid4(),
                {"workflow_id": str(workflow_id), "input_data": {"topic": "reorder"}},
            )

        assert result["status"] == "success"
        assert result["result"]["content"] == "workflow output"
        assert result["result"]["correlation"]["status"] == "registered"
        execute_kwargs = captured["execute"]
        assert execute_kwargs["agent_mode"] == "workflow"
        assert execute_kwargs["workflow_id"] == workflow_id
        assert execute_kwargs["input_data"] == {"topic": "reorder"}
        assert execute_kwargs["workspace_id"] == uuid.UUID(int=0)
        assert execute_kwargs["correlation"].workspace_id == uuid.UUID(int=0)
        assert execute_kwargs["session_id"] is not None
        init_kwargs = captured["init"]
        assert init_kwargs["entry_name"] == "scheduled-workflow"

    async def test_entry_failure_maps_to_failed(self, db_session: AsyncSession) -> None:
        from hecate.models.workflow import WorkflowModel

        workflow_id = uuid.uuid4()
        db_session.add(
            WorkflowModel(
                id=workflow_id,
                workspace_id=uuid.UUID(int=0),
                name="broken wf",
            )
        )
        await db_session.flush()

        class _BrokenEntry:
            def __init__(self, **kwargs: Any) -> None:
                pass

            async def execute(self, **kwargs: Any):
                raise RuntimeError("entry exploded")

        with (
            patch("hecate.core.database.async_session_factory", _session_factory(db_session)),
            patch("hecate.core.composition.runtime_port_adapter.create_runtime_port", MagicMock(return_value=None)),
            patch("hecate.execution.entry_service.EntryExecutionService", _BrokenEntry),
        ):
            result = await WorkflowExecutor().execute(uuid.uuid4(), {"workflow_id": str(workflow_id)})

        assert result["status"] == "failed"
        assert "entry exploded" in result["error"]

    async def test_scheduled_path_does_not_use_test_runner(self) -> None:
        """The scheduled executor source must not reference the studio
        test runner — that runner serves the studio test endpoint only."""
        import inspect

        from hecate.ops.scheduling.executors import WorkflowExecutor

        source = inspect.getsource(WorkflowExecutor)
        assert "WorkflowTestRunner" not in source
        assert "test_runner" not in source


async def test_real_scheduled_workflow_preserves_business_channels(db_session, default_workspace, monkeypatch):
    """The real graph branches on business input, with no service replacement."""
    from hecate.models.workflow import WorkflowModel, WorkflowVersionModel
    from hecate.runtime.eventstore import InMemoryEventStore
    from hecate.runtime.session_state import InMemorySessionStateStore

    workflow = WorkflowModel(name="channel-driven", workspace_id=default_workspace.id, execution_mode="task")
    db_session.add(workflow)
    await db_session.flush()
    db_session.add(
        WorkflowVersionModel(
            workflow_id=workflow.id,
            version=1,
            graph_dsl={
                "version": "1.0",
                "name": "channel-driven",
                "state": {
                    "messages": {"type": "topic", "reduce": "append"},
                    "topic": {"type": "last_value"},
                },
                "nodes": {
                    "choose": {"type": "condition", "config": {"expression": "topic == 'reorder'"}},
                    "accepted": {
                        "type": "variable-set",
                        "config": {
                            "variable_name": "messages",
                            "value": [{"role": "assistant", "content": "accepted"}],
                        },
                    },
                    "rejected": {
                        "type": "variable-set",
                        "config": {
                            "variable_name": "messages",
                            "value": [{"role": "assistant", "content": "rejected"}],
                        },
                    },
                },
                "edges": [{"source": "choose", "target": {"true": "accepted", "false": "rejected"}}],
                "entry": "choose",
            },
        )
    )
    await db_session.flush()
    store = InMemoryEventStore()
    monkeypatch.setattr("hecate.core.composition.entry_assembly.get_shared_event_store", lambda: store)
    monkeypatch.setattr(
        "hecate.core.composition.entry_assembly.get_shared_session_state_store", lambda: InMemorySessionStateStore()
    )
    input_data = {"messages": [{"role": "user", "content": "original input"}], "topic": "reorder"}
    with patch("hecate.core.database.async_session_factory", _session_factory(db_session)):
        result = await WorkflowExecutor().execute(
            uuid.uuid4(), {"workflow_id": str(workflow.id), "input_data": input_data}, workspace_id=default_workspace.id
        )
    assert result["status"] == "success", result
    assert result["result"]["content"] == "accepted"
    correlation = result["result"]["correlation"]
    assert correlation["status"] == "missing"
    assert "task_ref" in correlation
    assert "run_ref" not in correlation
    task = await db_session.get(TaskModel, uuid.UUID(correlation["task_ref"]["id"]))
    assert task.workspace_id == default_workspace.id
    all_events = [event for events in store._store.values() for event in events]
    assert any(event.payload.get("channel") == "topic" for event in all_events)


async def test_scheduled_workflow_rejects_foreign_workspace_before_dispatch(db_session, default_workspace):
    """Foreign and nonexistent targets produce the same response, without dispatch."""
    from hecate.models.workflow import WorkflowModel

    foreign = WorkflowModel(name="foreign private workflow", workspace_id=uuid.uuid4())
    db_session.add(foreign)
    await db_session.flush()
    with (
        patch("hecate.core.database.async_session_factory", _session_factory(db_session)),
        patch("hecate.execution.entry_service.EntryExecutionService") as entry,
    ):
        result = await WorkflowExecutor().execute(
            uuid.uuid4(), {"workflow_id": str(foreign.id)}, workspace_id=default_workspace.id
        )
    assert result == {"status": "failed", "error": f"Workflow {foreign.id} not found"}
    entry.assert_not_called()


@pytest.mark.parametrize("key", ["_user_id", "_tools", "sys.execution_mode"])
async def test_workflow_input_cannot_override_runtime_context(key):
    from hecate.studio.workflows.execution_service import WorkflowExecutionService

    service = WorkflowExecutionService(port=None)
    with pytest.raises(ValueError, match="runtime-owned"):
        await service.execute(agent_mode="workflow", input_data={key: "untrusted"})
