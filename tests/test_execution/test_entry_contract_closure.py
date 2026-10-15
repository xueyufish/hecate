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

from hecate.contracts.execution.events import EventKind
from hecate.contracts.execution.references import run_ref as make_run_ref
from hecate.core.composition.governance import BudgetRefusedError
from hecate.core.config import settings
from hecate.execution.entry_events import RunEventMapper
from hecate.execution.entry_service import CorrelationInput, EntryExecutionService
from hecate.models.agent import AgentModel
from hecate.runtime.errors import ExecutionStoppedError, MaxSuperstepsError
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
    fake_service["behavior"] = {
        "content": "ok",
        "finish_reason": "stop",
        "_engine_events": [{"type": "values", "state": {"secret": "internal"}}],
    }
    entry = EntryExecutionService(port=None, entry_name="contract-test", db=db_session, event_store=event_store)
    outcome = await entry.execute(
        agent_mode="chat",
        messages=[{"role": "user", "content": "hi"}],
        stream=False,
        correlation=CorrelationInput(),
    )
    assert not outcome.correlated
    assert outcome.result["content"] == "ok"
    assert "_engine_events" not in outcome.result


async def test_non_http_stream_persists_envelopes_and_bounded_summary(
    db_session: AsyncSession, default_workspace, fake_service, event_store
):
    """Persistence belongs to the shared entry, independent of HTTP rendering."""
    agent = await _agent_with_deployment(db_session, default_workspace.id)

    async def stream():
        yield {"type": "message", "content": "a" * 400}
        yield {"type": "message", "content": "b" * 400}

    fake_service["behavior"] = stream()
    entry = EntryExecutionService(port=None, entry_name="mcp", db=db_session, event_store=event_store)
    outcome = await entry.execute(
        stream=True, correlation=CorrelationInput(workspace_id=default_workspace.id, agent_id=agent.id)
    )
    chunks = [chunk async for chunk in outcome.result]
    assert len(chunks) == 2
    reader = RunEventMapper(outcome.correlation.task_ref, outcome.correlation.run_ref, event_store=event_store)
    page = await reader.read_events(limit=1)
    assert page.events[0].payload["content"] == "a" * 400
    assert page.has_more
    next_page = await reader.read_events(cursor=page.next_cursor)
    assert next_page.events[0].payload["content"] == "b" * 400
    # The terminal envelope closes the run's event view after the stream.
    assert len(next_page.events) == 2
    assert next_page.events[1].kind is EventKind.TERMINAL
    assert next_page.events[1].payload["state"] == "succeeded"
    assert not next_page.has_more
    from hecate.execution.task_run_registry import TaskRunRegistry

    run = await TaskRunRegistry(db_session).get_run(uuid.UUID(outcome.correlation.run_ref.id), default_workspace.id)
    assert run.projection == {"state": "succeeded", "summary": "a" * 400 + "b" * 100, "entry_name": "mcp"}


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


async def test_values_only_stream_retains_final_assistant_summary(
    db_session: AsyncSession, default_workspace, fake_service, event_store
):
    """A graph may publish its answer as state without provider token deltas."""
    agent = await _agent_with_deployment(db_session, default_workspace.id)

    async def stream():
        yield {"type": "values", "state": {"messages": [{"role": "assistant", "content": "graph answer"}]}}

    fake_service["behavior"] = stream()
    entry = EntryExecutionService(port=None, entry_name="workflow", db=db_session, event_store=event_store)
    outcome = await entry.execute(
        stream=True, correlation=CorrelationInput(workspace_id=default_workspace.id, agent_id=agent.id)
    )
    assert len([chunk async for chunk in outcome.result]) == 1
    from hecate.execution.task_run_registry import TaskRunRegistry

    run = await TaskRunRegistry(db_session).get_run(uuid.UUID(outcome.correlation.run_ref.id), default_workspace.id)
    assert run.projection == {"state": "succeeded", "summary": "graph answer", "entry_name": "workflow"}


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
    assert [envelope.payload.get("content") for envelope in page.events] == ["hello", "world", None]
    assert [envelope.kind for envelope in page.events] == [EventKind.EVENT, EventKind.EVENT, EventKind.TERMINAL]
    assert page.events[-1].payload["state"] == "succeeded"
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


async def test_sync_failure_maps_partial_engine_events_and_terminal_envelope(
    db_session: AsyncSession, default_workspace, fake_service, event_store
):
    """A failed synchronous run keeps the same evidence view as streaming."""
    import json

    from hecate.contracts.execution.references import run_ref as make_run_ref
    from hecate.contracts.execution.references import task_ref as make_task_ref

    agent = await _agent_with_deployment(db_session, default_workspace.id)
    boom = RuntimeError("boom with internal stack detail")
    boom.engine_events = [{"type": "message", "content": "partial"}]  # type: ignore[attr-defined]
    fake_service["behavior"] = boom

    entry = EntryExecutionService(port=None, entry_name="contract-test", db=db_session, event_store=event_store)
    with pytest.raises(RuntimeError):
        await entry.execute(
            agent_mode="chat",
            messages=[{"role": "user", "content": "hi"}],
            stream=False,
            correlation=CorrelationInput(workspace_id=default_workspace.id, agent_id=agent.id, goal="contract"),
        )

    correlation = boom.platform_correlation  # type: ignore[attr-defined]
    assert correlation["status"] == "registered"
    task_reference = make_task_ref(correlation["task_ref"]["issuer_domain"], correlation["task_ref"]["id"])
    run_reference = make_run_ref(correlation["run_ref"]["issuer_domain"], correlation["run_ref"]["id"])

    from hecate.execution.task_run_registry import TaskRunRegistry

    run = await TaskRunRegistry(db_session).get_run(uuid.UUID(run_reference.id), default_workspace.id)
    assert run.projection["state"] == "failed"
    assert run.projection["error_category"] == "execution_failed"

    reader = RunEventMapper(task_reference, run_reference, event_store=event_store)
    page = await reader.read_events()
    assert [e.kind for e in page.events] == [EventKind.EVENT, EventKind.TERMINAL]
    assert page.events[0].payload["content"] == "partial"
    terminal = page.events[1].payload
    assert terminal["state"] == "failed"
    assert terminal["stop_category"] == "execution_failed"
    assert terminal["error"] == {"type": "RuntimeError"}
    # Internal exception detail never enters the contract payload.
    assert "boom" not in json.dumps(terminal)


@pytest.mark.parametrize(
    ("failure", "category", "state"),
    [
        (lambda: ExecutionStoppedError("cancelled", "caller went away"), "cancelled", "cancelled"),
        (lambda: MaxSuperstepsError("loop"), "iteration_limit", "failed"),
        (lambda: BudgetRefusedError("over budget"), "budget_exhausted", "failed"),
    ],
)
async def test_sync_stop_categories_reach_envelope_and_projection(
    db_session: AsyncSession, default_workspace, fake_service, event_store, failure, category, state
):
    from hecate.contracts.execution.references import task_ref as make_task_ref

    agent = await _agent_with_deployment(db_session, default_workspace.id)
    boom = failure()
    fake_service["behavior"] = boom

    entry = EntryExecutionService(port=None, entry_name="contract-test", db=db_session, event_store=event_store)
    with pytest.raises(type(boom)):
        await entry.execute(
            agent_mode="chat",
            messages=[{"role": "user", "content": "hi"}],
            stream=False,
            correlation=CorrelationInput(workspace_id=default_workspace.id, agent_id=agent.id, goal="contract"),
        )

    correlation = boom.platform_correlation  # type: ignore[attr-defined]
    reader = RunEventMapper(
        make_task_ref(correlation["task_ref"]["issuer_domain"], correlation["task_ref"]["id"]),
        make_run_ref(correlation["run_ref"]["issuer_domain"], correlation["run_ref"]["id"]),
        event_store=event_store,
    )
    page = await reader.read_events()
    assert page.events[-1].kind is EventKind.TERMINAL
    assert page.events[-1].payload["state"] == state
    assert page.events[-1].payload["stop_category"] == category

    from hecate.execution.task_run_registry import TaskRunRegistry

    run = await TaskRunRegistry(db_session).get_run(uuid.UUID(correlation["run_ref"]["id"]), default_workspace.id)
    assert run.projection["error_category"] == category


async def test_streaming_failure_writes_terminal_envelope(
    db_session: AsyncSession, default_workspace, fake_service, event_store
):
    agent = await _agent_with_deployment(db_session, default_workspace.id)

    async def failing_stream():
        yield {"type": "message", "content": "partial"}
        raise ExecutionStoppedError("cancelled", "caller went away")

    fake_service["behavior"] = failing_stream()
    entry = EntryExecutionService(port=None, entry_name="mcp", db=db_session, event_store=event_store)
    outcome = await entry.execute(
        stream=True, correlation=CorrelationInput(workspace_id=default_workspace.id, agent_id=agent.id)
    )
    with pytest.raises(ExecutionStoppedError):
        async for _chunk in outcome.result:
            pass

    reader = RunEventMapper(outcome.correlation.task_ref, outcome.correlation.run_ref, event_store=event_store)
    page = await reader.read_events()
    assert [e.kind for e in page.events] == [EventKind.EVENT, EventKind.TERMINAL]
    assert page.events[-1].payload == {
        "state": "cancelled",
        "stop_category": "cancelled",
        "error": {"type": "ExecutionStoppedError"},
    }


async def test_declared_artifacts_reach_projection_and_terminal_envelope(
    db_session: AsyncSession, default_workspace, fake_service, event_store
):
    agent = await _agent_with_deployment(db_session, default_workspace.id)
    from hecate.models.evidence import EvidenceModel

    artifact = EvidenceModel(session_id=uuid.uuid4(), workspace_id=default_workspace.id, tool_name="report")
    db_session.add(artifact)
    await db_session.flush()
    artifact_ref = {"kind": "artifact", "issuer_domain": "hecate", "id": str(artifact.id)}
    fake_service["behavior"] = {
        "content": "report ready",
        "finish_reason": "stop",
        "declared_artifacts": [
            artifact_ref,
            # foreign issuer and wrong kind are rejected, not silently kept
            {"kind": "artifact", "issuer_domain": "foreign", "id": "art-2"},
            {"kind": "run", "issuer_domain": "hecate", "id": "run-9"},
        ],
    }

    entry = EntryExecutionService(port=None, entry_name="contract-test", db=db_session, event_store=event_store)
    outcome = await entry.execute(
        agent_mode="chat",
        messages=[{"role": "user", "content": "hi"}],
        stream=False,
        correlation=CorrelationInput(workspace_id=default_workspace.id, agent_id=agent.id),
    )

    # The declaration key never reaches protocol output.
    assert "declared_artifacts" not in outcome.result

    from hecate.execution.task_run_registry import TaskRunRegistry

    run = await TaskRunRegistry(db_session).get_run(uuid.UUID(outcome.correlation.run_ref.id), default_workspace.id)
    assert run.projection["artifacts"] == [artifact_ref]
    assert run.projection["state"] == "succeeded"  # rejection does not flip the terminal state

    reader = RunEventMapper(outcome.correlation.task_ref, outcome.correlation.run_ref, event_store=event_store)
    page = await reader.read_events()
    terminal = page.events[-1]
    assert terminal.kind is EventKind.TERMINAL
    assert terminal.payload["artifacts"] == [artifact_ref]
    rejected = terminal.extra["rejected_artifacts"]
    assert {r["reason"].split(":")[0] for r in rejected} == {"foreign issuer domain", "unsupported reference kind"}


async def test_request_side_artifact_injection_has_no_effect(
    db_session: AsyncSession, default_workspace, fake_service, event_store
):
    """Artifact declarations ride the engine result only; request
    parameters cannot mint references into the public contract."""
    agent = await _agent_with_deployment(db_session, default_workspace.id)
    fake_service["behavior"] = {"content": "plain answer", "finish_reason": "stop"}

    entry = EntryExecutionService(port=None, entry_name="contract-test", db=db_session, event_store=event_store)
    outcome = await entry.execute(
        agent_mode="chat",
        messages=[{"role": "user", "content": "hi"}],
        stream=False,
        declared_artifacts=[{"kind": "artifact", "issuer_domain": "hecate", "id": "smuggled"}],
        correlation=CorrelationInput(workspace_id=default_workspace.id, agent_id=agent.id),
    )

    from hecate.execution.task_run_registry import TaskRunRegistry

    run = await TaskRunRegistry(db_session).get_run(uuid.UUID(outcome.correlation.run_ref.id), default_workspace.id)
    assert "artifacts" not in run.projection
    reader = RunEventMapper(outcome.correlation.task_ref, outcome.correlation.run_ref, event_store=event_store)
    page = await reader.read_events()
    assert "artifacts" not in page.events[-1].payload
