"""Entry contract closure (step5d): terminal projection, envelope store.

Covers the entry service's correlate-before-execute ordering (a failed
execution leaves a registered Task/Run whose projection is finalized),
the stop-category mapping, the streaming wrapper, and the envelope sink
that lets a fresh mapper page a run's platform events from the event
store instead of the originating process's memory.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.contracts.execution.references import run_ref as make_run_ref
from hecate.core.config import settings
from hecate.execution.entry_events import RunEventMapper
from hecate.execution.entry_service import CorrelationInput, EntryExecutionService
from hecate.models.agent import AgentModel
from hecate.runtime.errors import ExecutionStoppedError
from hecate.runtime.eventstore import InMemoryEventStore


async def _agent_with_deployment(db_session: AsyncSession, workspace_id: uuid.UUID) -> AgentModel:
    """Mirror the g3 harness: agent + principal + default deployment."""
    from tests.test_channel.test_chat_engine_g3_entry import _agent_with_tool

    return await _agent_with_tool(db_session, workspace_id, with_deployment=True)


class _FakeWorkflowService:
    """Scriptable stand-in at the WorkflowExecutionService seam."""

    script: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        pass

    async def execute(self, **kwargs: Any) -> Any:
        behavior = self.script.pop("behavior")
        if isinstance(behavior, Exception):
            raise behavior
        return behavior


@pytest.fixture
def fake_service(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr("hecate.studio.workflows.execution_service.WorkflowExecutionService", _FakeWorkflowService)
    return _FakeWorkflowService.script


@pytest.fixture
def event_store() -> InMemoryEventStore:
    return InMemoryEventStore()


async def test_success_execution_finalizes_succeeded_with_summary(
    db_session: AsyncSession, default_workspace, fake_service, event_store
):
    agent = await _agent_with_deployment(db_session, default_workspace.id)
    fake_service["behavior"] = {"content": "final answer", "finish_reason": "stop"}

    entry = EntryExecutionService(
        port=None,
        entry_name="contract-test",
        db=db_session,
        event_store=event_store,
    )
    outcome = await entry.execute(
        agent_mode="chat",
        messages=[{"role": "user", "content": "hi"}],
        stream=False,
        correlation=CorrelationInput(
            workspace_id=default_workspace.id,
            agent_id=agent.id,
            goal="contract",
        ),
    )

    assert outcome.correlated
    from hecate.execution.task_run_registry import TaskRunRegistry

    run = await TaskRunRegistry(db_session).get_run(uuid.UUID(outcome.correlation.run_ref.id), default_workspace.id)
    assert run.projection["state"] == "succeeded"
    assert run.projection["summary"] == "final answer"
    assert "error_category" not in run.projection
    await db_session.commit()


async def test_stop_failure_finalizes_failed_with_category(
    db_session: AsyncSession, default_workspace, fake_service, event_store
):
    agent = await _agent_with_deployment(db_session, default_workspace.id)
    fake_service["behavior"] = ExecutionStoppedError("needs_review", "outcome not established")

    entry = EntryExecutionService(
        port=None,
        entry_name="contract-test",
        db=db_session,
        event_store=event_store,
    )
    with pytest.raises(ExecutionStoppedError) as exc_info:
        await entry.execute(
            agent_mode="chat",
            messages=[{"role": "user", "content": "hi"}],
            stream=False,
            correlation=CorrelationInput(
                workspace_id=default_workspace.id,
                agent_id=agent.id,
                goal="contract",
            ),
        )

    # The registered correlation rides the exception for terminal payloads.
    assert exc_info.value.platform_correlation["status"] == "registered"

    from hecate.execution.task_run_registry import TaskRunRegistry

    run = await TaskRunRegistry(db_session).get_run(
        uuid.UUID(exc_info.value.platform_correlation["run_ref"]["id"]), default_workspace.id
    )
    assert run.projection["state"] == "failed"
    assert run.projection["error_category"] == "needs_review"
    await db_session.commit()


async def test_generic_failure_maps_to_stable_category(
    db_session: AsyncSession, default_workspace, fake_service, event_store
):
    agent = await _agent_with_deployment(db_session, default_workspace.id)
    fake_service["behavior"] = RuntimeError("secret internal detail: /etc/passwd")

    entry = EntryExecutionService(
        port=None,
        entry_name="contract-test",
        db=db_session,
        event_store=event_store,
    )
    with pytest.raises(RuntimeError) as exc_info:
        await entry.execute(
            agent_mode="chat",
            messages=[{"role": "user", "content": "hi"}],
            stream=False,
            correlation=CorrelationInput(
                workspace_id=default_workspace.id,
                agent_id=agent.id,
                goal="contract",
            ),
        )

    from hecate.execution.task_run_registry import TaskRunRegistry

    correlation = exc_info.value.platform_correlation
    run = await TaskRunRegistry(db_session).get_run(uuid.UUID(correlation["run_ref"]["id"]), default_workspace.id)
    assert run.projection["state"] == "failed"
    assert run.projection["error_category"] == "execution_failed"
    assert "secret" not in str(run.projection)
    await db_session.commit()


async def test_missing_correlation_skips_finalization(db_session: AsyncSession, fake_service, event_store):
    fake_service["behavior"] = {"content": "ok", "finish_reason": "stop"}
    entry = EntryExecutionService(port=None, entry_name="contract-test", db=db_session, event_store=event_store)
    outcome = await entry.execute(
        agent_mode="chat",
        messages=[{"role": "user", "content": "hi"}],
        stream=False,
        correlation=CorrelationInput(),
    )
    assert not outcome.correlated
    assert outcome.result["content"] == "ok"


async def test_streaming_failure_finalizes_cancelled(
    db_session: AsyncSession, default_workspace, fake_service, event_store
):
    agent = await _agent_with_deployment(db_session, default_workspace.id)

    async def failing_stream():
        yield {"type": "message", "content": "partial"}
        raise ExecutionStoppedError("cancelled", "caller went away")

    fake_service["behavior"] = failing_stream()

    entry = EntryExecutionService(
        port=None,
        entry_name="contract-test",
        db=db_session,
        event_store=event_store,
    )
    outcome = await entry.execute(
        agent_mode="chat",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
        correlation=CorrelationInput(
            workspace_id=default_workspace.id,
            agent_id=agent.id,
            goal="contract",
        ),
    )
    collected: list = []
    with pytest.raises(ExecutionStoppedError):
        async for chunk in outcome.result:
            collected.append(chunk)
    assert collected == [{"type": "message", "content": "partial"}]

    from hecate.execution.task_run_registry import TaskRunRegistry

    run = await TaskRunRegistry(db_session).get_run(uuid.UUID(outcome.correlation.run_ref.id), default_workspace.id)
    assert run.projection["state"] == "cancelled"
    assert run.projection["error_category"] == "cancelled"
    await db_session.commit()


async def test_envelopes_persist_to_store_and_page_across_mappers(
    db_session: AsyncSession, default_workspace, fake_service, event_store
):
    agent = await _agent_with_deployment(db_session, default_workspace.id)
    fake_service["behavior"] = {
        "content": "answer",
        "finish_reason": "stop",
        "_engine_events": [
            {"type": "message", "content": "hello"},
            {"type": "message", "content": "world"},
        ],
    }

    entry = EntryExecutionService(
        port=None,
        entry_name="contract-test",
        db=db_session,
        event_store=event_store,
    )
    outcome = await entry.execute(
        agent_mode="chat",
        messages=[{"role": "user", "content": "hi"}],
        stream=False,
        correlation=CorrelationInput(
            workspace_id=default_workspace.id,
            agent_id=agent.id,
            goal="contract",
        ),
    )

    # The engine-event key is popped: protocol output stays clean.
    assert "_engine_events" not in outcome.result

    # A fresh mapper over the same refs (a different request/process)
    # pages the persisted envelopes from the store, not its own memory.
    fresh = RunEventMapper(outcome.correlation.task_ref, outcome.correlation.run_ref, event_store=event_store)
    page = await fresh.read_events()
    assert [envelope.payload.get("content") for envelope in page.events] == ["hello", "world"]
    assert page.has_more is False


async def test_mapper_without_store_falls_back_to_memory(default_workspace):
    task_ref = None
    from hecate.contracts.execution.references import task_ref as make_task_ref

    task_ref = make_task_ref("hecate", str(uuid.uuid4()))
    run_reference = make_run_ref("hecate", str(uuid.uuid4()))
    mapper = RunEventMapper(task_ref, run_reference)
    await mapper.map_stream_event({"type": "message", "content": "kept"})
    page = await mapper.read_events()
    assert [envelope.payload.get("content") for envelope in page.events] == ["kept"]


async def test_engine_flag_setting_untouched():
    """Sanity: the closure never touches the G3 routing flag."""
    assert isinstance(settings.CHAT_TOOL_LOOP_ENGINE_ENABLED, bool)
