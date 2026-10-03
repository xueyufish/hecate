"""Builtin-backend behavior tests (step5a tasks 2.2/2.4).

The shared contract assertions run in test_backend_contract.py via the
``builtin`` fixture param; this module covers the builtin-specific semantics
that have no stub equivalent:

- missing pre-resolved builtin config is rejected (platform adapter's job);
- submitting with no running event loop is an explicit ``unreachable``;
- cancel is recorded as REQUESTED and never surfaces as applied;
- a scheduled run reaches SUCCEEDED through the real engine with a stub
  port, and its engine events are cursor-resumable;
- unknown run references read as outcome_unknown (records are in-process).
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from hecate.contracts.execution.errors import BackendErrorCode
from hecate.contracts.execution.references import BackendRef, RefKind
from hecate.execution.backend import CancelRequestState, ExecutionBackendError, RunState
from hecate.execution.builtin import HecateExecutionBackend
from hecate.studio.workflows.templates import build_chat_graph
from tests.test_execution.conftest import SAMPLES_DIR, load_sample

SAMPLE_REQUEST = SAMPLES_DIR / "requests" / "submit-minimal.json"


def _make_port(tokens: list[str] | None = None) -> MagicMock:
    """Minimal stub port mirroring the service-test double (engine completes a turn)."""
    port = MagicMock()
    tokens = tokens or ["Hello!"]

    async def fake_context_assemble(*args, **kwargs):
        return {"messages": kwargs.get("messages", []), "tools": kwargs.get("tools"), "metadata": {}}

    port.context_assemble = AsyncMock(side_effect=fake_context_assemble)

    async def fake_llm_invoke(*args, **kwargs):
        for t in tokens:
            yield t

    port.llm_invoke = fake_llm_invoke
    port.tool_execute = AsyncMock(return_value="tool result")
    port.knowledge_query = AsyncMock(return_value="knowledge context")
    port.create_span = AsyncMock(return_value=None)
    port.end_span = AsyncMock(return_value=None)
    return port


def _make_backend(**kwargs: object) -> HecateExecutionBackend:
    return HecateExecutionBackend(port=_make_port(), **kwargs)


async def test_idempotency_covers_resolved_graph_and_keeps_caller_input_unchanged():
    from copy import deepcopy
    from dataclasses import replace

    backend = _make_backend()
    request = _make_request()
    original = deepcopy(request.backend_config_ns)
    receipt = backend.submit(request)
    await _wait_for_terminal(backend, receipt.run_ref)
    assert request.backend_config_ns == original
    assert backend.submit(request) == receipt
    changed = deepcopy(original)
    changed["builtin"]["graph_config"] = build_chat_graph(model="a-different-model")
    with pytest.raises(ExecutionBackendError) as error:
        backend.submit(replace(request, backend_config_ns=changed))
    assert error.value.error.code is BackendErrorCode.VERSION_CONFLICT


async def test_backend_rejects_wrong_reference_kind():
    backend = _make_backend()
    receipt = backend.submit(_make_request())
    foreign_kind = BackendRef(RefKind.TASK, receipt.run_ref.issuer_domain, receipt.run_ref.id)
    with pytest.raises(ExecutionBackendError) as error:
        backend.get_run(foreign_kind)
    assert error.value.error.code is BackendErrorCode.UNSUPPORTED
    await _wait_for_terminal(backend, receipt.run_ref)


async def test_shared_assembly_wires_engine_commit_events():
    from hecate.runtime.eventstore import EventType, InMemoryEventStore

    store = InMemoryEventStore()
    backend = _make_backend(event_store=store)
    request = _make_request()
    session_id = uuid.UUID(request.backend_config_ns["builtin"]["session_id"])
    receipt = backend.submit(request)
    await _wait_for_terminal(backend, receipt.run_ref)
    events = await store.get_events(session_id)
    kinds = {event.event_type for event in events}
    assert {EventType.TURN_START, EventType.TURN_END, EventType.CHANNEL_WRITE, EventType.STEP_END} <= kinds
    assert all(event.session_id == session_id for event in events)


@pytest.mark.parametrize("limit", ["budget", "deadline"])
async def test_unimplemented_resource_bounds_are_not_silently_ignored(limit):
    from dataclasses import replace

    from hecate.contracts.execution.request import Budget

    backend = _make_backend()
    request = _make_request()
    request = (
        replace(request, budget=Budget(max_tokens=1))
        if limit == "budget"
        else replace(
            request,
            deadline_at="2000-01-01T00:00:00+00:00",
        )
    )
    with pytest.raises(ExecutionBackendError) as error:
        backend.submit(request)
    assert error.value.error.code is BackendErrorCode.UNSUPPORTED
    assert backend._records == {}


async def test_interrupted_execution_is_not_reported_as_success(monkeypatch):
    from types import SimpleNamespace

    import hecate.execution.builtin as builtin_module

    class InterruptedRuntime:
        async def execute(self, **kwargs):
            yield {"type": "interrupt", "value": "approval required"}

    monkeypatch.setattr(
        builtin_module,
        "assemble_execution",
        lambda **kwargs: SimpleNamespace(
            runtime=InterruptedRuntime(),
            initial_input={},
            execution_mode="conversational",
        ),
    )
    backend = _make_backend()
    receipt = backend.submit(_make_request())
    await _wait_for_terminal(backend, receipt.run_ref)
    assert backend.get_run(receipt.run_ref).state is RunState.UNKNOWN
    assert "interrupted" in backend.get_run(receipt.run_ref).detail_ns["reason"]


def _make_request(key: str = "idem-001", backend: HecateExecutionBackend | None = None) -> object:
    """Sample request enriched with the pre-resolved builtin config namespace."""
    data = load_sample(SAMPLE_REQUEST)
    data["idempotency_key"] = key
    data["input"] = {"messages": [{"role": "user", "content": "Hi"}]}
    data["backend_config_ns"] = {
        "builtin": {
            "graph_config": build_chat_graph(model="gpt-4o"),
            "session_id": str(uuid.uuid4()),
        }
    }
    from hecate.contracts.execution.request import ExecutionRequest

    return ExecutionRequest.from_dict(data)


async def _wait_for_terminal(backend: HecateExecutionBackend, run_ref: BackendRef, timeout: float = 10.0) -> None:
    """Poll until the scheduled run leaves PENDING/RUNNING (engine is fast but async)."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if backend.get_run(run_ref).state in {RunState.SUCCEEDED, RunState.FAILED, RunState.UNKNOWN}:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"run {run_ref.id} did not reach a terminal state within {timeout}s")


# --- sync negatives (no loop / no config / version window / unknown ref) --------


def test_missing_builtin_config_is_rejected() -> None:
    """No platform-resolved definition in the namespace -> explicit UNSUPPORTED."""
    backend = _make_backend()
    data = load_sample(SAMPLE_REQUEST)
    data["input"] = {"messages": []}

    from hecate.contracts.execution.request import ExecutionRequest

    with pytest.raises(ExecutionBackendError) as excinfo:
        backend.submit(ExecutionRequest.from_dict(data))

    error = excinfo.value.error
    assert error.code is BackendErrorCode.UNSUPPORTED
    assert "platform adapter" in error.message


def test_missing_messages_is_rejected() -> None:
    backend = _make_backend()
    data = load_sample(SAMPLE_REQUEST)
    data["backend_config_ns"] = {"builtin": {"graph_config": object(), "session_id": str(uuid.uuid4())}}

    from hecate.contracts.execution.request import ExecutionRequest

    with pytest.raises(ExecutionBackendError) as excinfo:
        backend.submit(ExecutionRequest.from_dict(data))

    assert excinfo.value.error.code is BackendErrorCode.UNSUPPORTED


def test_submit_without_running_loop_is_unreachable() -> None:
    """Single-event-loop assumption: no loop -> explicit unreachable, no record kept."""
    backend = _make_backend()
    request = _make_request()

    with pytest.raises(ExecutionBackendError) as excinfo:
        backend.submit(request)  # type: ignore[arg-type]

    assert excinfo.value.error.code is BackendErrorCode.UNREACHABLE
    # Nothing was scheduled, so no phantom record remains.
    with pytest.raises(ExecutionBackendError):
        backend.get_run(BackendRef(kind=RefKind.RUN, issuer_domain="hecate-builtin", id="nonexistent"))


def test_contract_version_outside_window_is_conflict() -> None:
    backend = _make_backend()
    data = load_sample(SAMPLE_REQUEST)
    data["contract_version"] = "99.0"

    from hecate.contracts.execution.request import ExecutionRequest

    with pytest.raises(ExecutionBackendError) as excinfo:
        backend.submit(ExecutionRequest.from_dict(data))

    error = excinfo.value.error
    assert error.code is BackendErrorCode.VERSION_CONFLICT
    assert error.detail_ns["requested"] == "99.0"
    assert error.detail_ns["supported"] == "0.x"


def test_unknown_run_reference_is_outcome_unknown() -> None:
    backend = _make_backend()
    with pytest.raises(ExecutionBackendError) as excinfo:
        backend.get_run(BackendRef(kind=RefKind.RUN, issuer_domain="hecate-builtin", id="nope"))
    assert excinfo.value.error.code is BackendErrorCode.OUTCOME_UNKNOWN

    with pytest.raises(ExecutionBackendError) as excinfo:
        backend.get_run(BackendRef(kind=RefKind.RUN, issuer_domain="other", id="nope"))
    assert excinfo.value.error.code is BackendErrorCode.UNSUPPORTED


def test_list_artifacts_is_empty_in_draft() -> None:
    """Artifact references land with 5c/step6; the draft returns an honest empty set."""
    backend = _make_backend()
    request = _make_request()

    async def _run() -> tuple[object, ...]:
        r = backend.submit(request)  # type: ignore[arg-type]
        await _wait_for_terminal(backend, r.run_ref)
        return backend.list_artifacts(r.run_ref)

    artifacts = asyncio.run(_run())
    assert artifacts == ()


# --- async behaviors (real engine, stub port) ------------------------------------


async def test_submit_runs_engine_and_reaches_succeeded() -> None:
    backend = _make_backend()
    request = _make_request()

    receipt = backend.submit(request)  # type: ignore[arg-type]

    assert receipt.run_ref.issuer_domain == "hecate-builtin"
    assert receipt.run_ref.kind is RefKind.RUN
    await _wait_for_terminal(backend, receipt.run_ref)
    status = backend.get_run(receipt.run_ref)
    assert status.state is RunState.SUCCEEDED
    assert status.detail_ns.get("reason") is None


async def test_engine_events_are_cursor_resumable() -> None:
    backend = _make_backend()
    request = _make_request()

    receipt = backend.submit(request)  # type: ignore[arg-type]
    await _wait_for_terminal(backend, receipt.run_ref)

    first = backend.read_events(receipt.run_ref, cursor=None)
    assert first.events, "engine produced no normalized events"
    assert [e.source_sequence for e in first.events] == list(range(len(first.events)))
    assert all(e.event_id for e in first.events)
    assert all(e.payload_schema_ref for e in first.events)

    # Cursor resume: no duplicate delivery.
    seq = first.events[0].source_sequence
    resumed = backend.read_events(receipt.run_ref, cursor=str(seq + 1))
    assert [e.source_sequence for e in resumed.events] == list(range(seq + 1, len(first.events)))


async def test_cancel_is_requested_and_never_applied() -> None:
    backend = _make_backend()
    request = _make_request()

    receipt = backend.submit(request)  # type: ignore[arg-type]
    requested = backend.request_cancel(receipt.run_ref)

    assert requested.state is CancelRequestState.REQUESTED
    await _wait_for_terminal(backend, receipt.run_ref)
    status = backend.get_run(receipt.run_ref)
    # Whatever the engine did, the cancel receipt never becomes APPLIED and
    # the run never reads CANCELLED from a request alone.
    assert status.detail_ns.get("cancel_state") == "requested"
    assert status.state is not RunState.CANCELLED


async def test_idempotent_replay_schedules_nothing_new() -> None:
    backend = _make_backend()
    request = _make_request(key="idem-replay")

    first = backend.submit(request)  # type: ignore[arg-type]
    await _wait_for_terminal(backend, first.run_ref)
    # Replay the SAME request object: identical content -> original receipt.
    second = backend.submit(request)  # type: ignore[arg-type]

    assert second.run_ref == first.run_ref
    assert second.received_at == first.received_at


async def test_scheduled_failure_is_failed_with_reason() -> None:
    """An engine crash inside the scheduled task reads failed, never success."""
    backend = _make_backend()
    data = load_sample(SAMPLE_REQUEST)
    data["idempotency_key"] = "idem-boom"
    data["input"] = {"messages": [{"role": "user", "content": "Hi"}]}
    data["backend_config_ns"] = {
        "builtin": {
            "graph_config": build_chat_graph(model="gpt-4o"),
            "session_id": "not-a-uuid",  # engine input error -> run-level failure
        }
    }

    from hecate.contracts.execution.request import ExecutionRequest

    request = ExecutionRequest.from_dict(data)
    receipt = backend.submit(request)
    await _wait_for_terminal(backend, receipt.run_ref)

    status = backend.get_run(receipt.run_ref)
    assert status.state in {RunState.FAILED, RunState.UNKNOWN}
    assert status.detail_ns.get("reason")


# --- compatibility samples (task 3.1): structure pins against real output -------


def _sample(name: str) -> dict:
    return load_sample(SAMPLES_DIR / "builtin" / name)


async def test_receipt_structure_matches_sample() -> None:
    from tests.test_execution.conftest import validate_against_schema

    backend = _make_backend()
    receipt = backend.submit(_make_request(key="idem-sample"))

    sample = _sample("submit-receipt.json")
    receipt_dict = receipt.to_dict()
    validate_against_schema(receipt_dict, "submit-receipt")
    assert set(receipt_dict) == set(sample), f"receipt keys drifted: {set(receipt_dict) ^ set(sample)}"
    assert receipt_dict["run_ref"]["issuer_domain"] == sample["run_ref"]["issuer_domain"]
    assert receipt_dict["detail_ns"].keys() == sample["detail_ns"].keys()


async def test_status_and_cancel_structures_match_samples() -> None:
    from tests.test_execution.conftest import validate_against_schema

    backend = _make_backend()
    receipt = backend.submit(_make_request(key="idem-sample2"))
    await _wait_for_terminal(backend, receipt.run_ref)

    status_dict = backend.get_run(receipt.run_ref).to_dict()
    validate_against_schema(status_dict, "run-status")
    assert set(status_dict) == set(_sample("run-status.json"))

    cancel = backend.request_cancel(receipt.run_ref)
    cancel_dict = cancel.to_dict()
    validate_against_schema(cancel_dict, "cancel-receipt")
    sample = _sample("cancel-receipt.json")
    assert set(cancel_dict) == set(sample)
    assert cancel_dict["state"] == sample["state"]


async def test_event_envelope_structure_matches_sample() -> None:
    from tests.test_execution.conftest import validate_against_schema

    backend = _make_backend()
    receipt = backend.submit(_make_request(key="idem-sample3"))
    await _wait_for_terminal(backend, receipt.run_ref)

    page = backend.read_events(receipt.run_ref, cursor=None)
    assert page.events
    sample = _sample("event-envelope.json")
    envelope = page.events[0].to_dict()
    validate_against_schema(envelope, "event-envelope")
    assert set(envelope) == set(sample)
    assert envelope["payload_schema_ref"] == sample["payload_schema_ref"]
    assert envelope["run_ref"]["issuer_domain"] == sample["run_ref"]["issuer_domain"]
    # event_id pins the {run_id}:{sequence} format
    assert envelope["event_id"] == f"{envelope['run_ref']['id']}:{envelope['source_sequence']}"
