"""Regression checks for the actual step5d entry contract boundaries."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from hecate.execution.entry_events import RunEventMapper
from hecate.execution.entry_service import CorrelationInput, EntryExecutionService
from hecate.models.evidence import EvidenceModel
from hecate.runtime.eventstore import InMemoryEventStore
from tests.test_execution.conftest import validate_against_schema
from tests.test_execution.test_entry_contract_closure import _agent_with_deployment


@pytest.fixture
def entry_script(monkeypatch):
    script = {}

    class StubWorkflowService:
        """Return the scripted response without replacing entry registration."""

        def __init__(self, **kwargs):
            pass

        async def execute(self, **kwargs):
            return script["result"]

    monkeypatch.setattr("hecate.studio.workflows.execution_service.WorkflowExecutionService", StubWorkflowService)
    return script


async def test_terminal_envelope_matches_published_schema():
    from hecate.contracts.execution.references import run_ref, task_ref

    mapper = RunEventMapper(task_ref("hecate", str(uuid.uuid4())), run_ref("hecate", str(uuid.uuid4())))
    terminal = await mapper.write_terminal_envelope(state="failed", stop_category="execution_failed")
    validate_against_schema(terminal.to_dict(), "event-envelope")


async def test_same_issuer_foreign_workspace_artifact_is_rejected(db_session, default_workspace, entry_script):
    agent = await _agent_with_deployment(db_session, default_workspace.id)
    foreign = EvidenceModel(
        session_id=uuid.uuid4(), workspace_id=uuid.uuid4(), tool_name="foreign-report", raw_content="private"
    )
    db_session.add(foreign)
    await db_session.flush()
    reference = {"kind": "artifact", "issuer_domain": "hecate", "id": str(foreign.id)}
    entry_script["result"] = {"content": "done", "declared_artifacts": [reference]}
    store = InMemoryEventStore()
    entry = EntryExecutionService(port=None, entry_name="review", db=db_session, event_store=store)
    outcome = await entry.execute(correlation=CorrelationInput(workspace_id=default_workspace.id, agent_id=agent.id))
    reader = RunEventMapper(outcome.correlation.task_ref, outcome.correlation.run_ref, event_store=store)
    terminal = (await reader.read_events()).events[-1]
    assert not terminal.payload.get("artifacts")
    assert terminal.extra.get("rejected_artifacts")


@pytest.mark.parametrize("stop", ["cancel", "close"])
async def test_stream_task_cancel_or_close_records_terminal(db_session, default_workspace, entry_script, stop):
    agent = await _agent_with_deployment(db_session, default_workspace.id)

    async def stream():
        yield {"type": "message", "content": "partial"}
        if stop == "cancel":
            raise asyncio.CancelledError()
        yield {"type": "message", "content": "unreachable"}

    entry_script["result"] = stream()
    store = InMemoryEventStore()
    entry = EntryExecutionService(port=None, entry_name="review", db=db_session, event_store=store)
    outcome = await entry.execute(correlation=CorrelationInput(workspace_id=default_workspace.id, agent_id=agent.id))
    assert (await anext(outcome.result))["content"] == "partial"
    if stop == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await anext(outcome.result)
    else:
        await outcome.result.aclose()
    reader = RunEventMapper(outcome.correlation.task_ref, outcome.correlation.run_ref, event_store=store)
    terminal = (await reader.read_events()).events[-1]
    assert terminal.payload.get("state") == "cancelled"
    assert terminal.payload.get("stop_category") == "cancelled"


async def test_real_adapter_preserves_final_artifact_declarations():
    from hecate.studio.workflows.execution_service import WorkflowExecutionService

    declaration = [{"kind": "artifact", "issuer_domain": "hecate", "id": str(uuid.uuid4())}]

    class StubRuntime:
        """Emit a completed graph state carrying its declared outputs."""

        async def execute(self, **kwargs):
            yield {
                "type": "values",
                "state": {"messages": [{"role": "assistant", "content": "done"}], "declared_artifacts": declaration},
            }

    result = await WorkflowExecutionService(port=None)._non_stream_execute(
        runtime=StubRuntime(), session_id=uuid.uuid4(), initial_input={}
    )
    assert result.get("declared_artifacts") == declaration


def test_output_artifact_declarations_exclude_supplied_history():
    import json

    from hecate.core.output_artifacts import declared_output_artifacts

    ref = {"kind": "artifact", "issuer_domain": "hecate", "id": str(uuid.uuid4())}
    supplied = {"role": "tool", "content": json.dumps({"declared_artifacts": [ref]})}
    assert declared_output_artifacts({"messages": [supplied]}, input_message_count=1) is None
    assert declared_output_artifacts({"messages": [supplied, supplied]}, input_message_count=1) == [ref]


async def test_workflow_input_cannot_declare_output_artifacts():
    from hecate.studio.workflows.execution_service import WorkflowExecutionService

    with pytest.raises(ValueError, match="runtime-owned channels"):
        await WorkflowExecutionService(port=None).execute(agent_mode="workflow", input_data={"declared_artifacts": []})


def test_registry_factory_resolves_without_asgi_lifespan(monkeypatch):
    from hecate.core import database
    from hecate.core.composition import entry_assembly

    factory = object()
    monkeypatch.setattr(database, "async_session_factory", factory)
    monkeypatch.setattr(entry_assembly, "_shared_registry_session_factory", entry_assembly._UNREGISTERED_FACTORY)
    assert entry_assembly.get_shared_registry_session_factory() is factory


async def test_failed_event_append_does_not_create_a_phantom_sequence(monkeypatch):
    from hecate.contracts.execution.references import run_ref, task_ref

    store = InMemoryEventStore()
    append = store.append

    async def unavailable(event):
        raise OSError("event storage unavailable")

    mapper = RunEventMapper(
        task_ref("hecate", str(uuid.uuid4())), run_ref("hecate", str(uuid.uuid4())), event_store=store
    )
    monkeypatch.setattr(store, "append", unavailable)
    with pytest.raises(OSError):
        await mapper.map_stream_event({"type": "message", "content": "not persisted"})
    assert not (await mapper.read_events()).events
    monkeypatch.setattr(store, "append", append)
    first = await mapper.map_stream_event({"type": "message", "content": "persisted"})
    terminal = await mapper.write_terminal_envelope(state="succeeded")
    assert (first.source_sequence, terminal.source_sequence) == (0, 1)
    page = await mapper.read_events(limit=1)
    assert (await mapper.read_events(cursor=page.next_cursor)).events == (terminal,)
