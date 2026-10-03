"""Contract tests for AgentExecutionBackend (tasks 3.2 / 3.3).

The same assertions any real backend must satisfy; the stub is simply the
first implementation under test. Key invariants:

- unsupported capabilities surface structured UNSUPPORTED errors (the stub's
  permanent pause/resume/export_context negative case) - never fabricated
  success;
- submit is idempotent by idempotency key; same key with different content is
  a version_conflict pointing at the mismatch;
- a lost submit response is outcome_unknown with a reconciliation hint, and
  resubmitting the same key returns the original receipt - the run is never
  silently marked failed;
- event cursors are opaque and resumable; gaps are explicit markers;
- a cancel request (REQUESTED) is distinct from an applied cancel (APPLIED);
- contract-version negotiation rejects unsupported windows with
  version_conflict.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hecate.contracts.execution.capabilities import BackendCapabilities, CapabilityLevel
from hecate.contracts.execution.errors import BackendErrorCode
from hecate.contracts.execution.events import EventKind
from hecate.contracts.execution.references import RefKind
from hecate.contracts.execution.request import ExecutionRequest
from hecate.execution.backend import (
    AgentExecutionBackend,
    CancelRequestState,
    ExecutionBackendError,
    RunState,
    UnsupportedCapabilityError,
)
from hecate.execution.stub import StubExecutionBackend
from tests.test_execution.conftest import SAMPLES_DIR, load_sample

SAMPLE_REQUEST = SAMPLES_DIR / "requests" / "submit-minimal.json"

# Stub-only injection points (append_event / inject_gap / settle_cancel /
# unreachable_submit / detail_ns shapes) below this line are intentionally
# NOT parametrized: they exercise stub-internal mechanics whose live
# equivalents are covered by test_live_pilot.py through the binding.


def make_request(tmp_path: Path, key: str = "idem-001") -> ExecutionRequest:
    data = load_sample(SAMPLE_REQUEST)
    data["idempotency_key"] = key
    return ExecutionRequest.from_dict(data)


def _builtin_request_factory() -> tuple[AgentExecutionBackend, object]:
    """Builtin backend + request factory pre-loaded with a resolvable config.

    The builtin backend rejects requests without ``backend_config_ns["builtin"]``
    (definitions are the platform adapter's job), so its factory injects a
    chat-graph config and session id the shared assembly can execute against
    the stub port.
    """
    import uuid as uuid_mod

    from hecate.execution.builtin import HecateExecutionBackend
    from hecate.studio.workflows.templates import build_chat_graph
    from tests.test_execution.test_builtin_backend import _make_port

    backend = HecateExecutionBackend(port=_make_port())

    def make_builtin_request(tmp_path: Path, key: str = "idem-001") -> ExecutionRequest:
        data = load_sample(SAMPLE_REQUEST)
        data["idempotency_key"] = key
        data["input"] = {"messages": [{"role": "user", "content": "Hi"}]}
        data["backend_config_ns"] = {
            "builtin": {
                "graph_config": build_chat_graph(model="gpt-4o"),
                # Retries retain their resolved definition; a fresh session
                # would be a different execution request.
                "session_id": str(uuid_mod.uuid5(uuid_mod.NAMESPACE_URL, f"builtin-contract:{key}")),
            }
        }
        return ExecutionRequest.from_dict(data)

    return backend, make_builtin_request


@pytest.fixture(params=["stub", "live", "builtin"])
def backend(request) -> tuple[AgentExecutionBackend, str, object]:
    """Run shared contract assertions against stub, live pilot, AND builtin.

    The second element is the issuer domain the backend signs its receipts
    with; the third is the request factory each backend needs (builtin
    requires a pre-resolved config namespace, the others take the plain
    sample). The live fixture is resolved lazily so local runs never touch
    the pilot environment. Builtin-specific semantics (cancel receipts,
    unknown runs, paging) live in test_builtin_backend.py.
    """
    if request.param == "stub":
        return StubExecutionBackend(), "stub", make_request
    if request.param == "live":
        return request.getfixturevalue("live_backend"), "pilot-ts", make_request
    backend_obj, factory = _builtin_request_factory()
    return backend_obj, "hecate-builtin", factory


def test_standing_unsupported_negative_case_is_declared(backend) -> None:
    candidate, _, _ = backend
    caps = candidate.describe_capabilities()
    assert caps.level_of("pause") is CapabilityLevel.UNSUPPORTED
    assert caps.level_of("resume") is CapabilityLevel.UNSUPPORTED
    assert caps.level_of("export_context") is CapabilityLevel.UNSUPPORTED


async def test_pause_on_unsupported_backend_is_structured_error(backend, tmp_path) -> None:
    backend_obj, _, make_req = backend
    request = make_req(tmp_path)
    receipt = backend_obj.submit(request)

    with pytest.raises(UnsupportedCapabilityError) as excinfo:
        backend_obj.pause(receipt.run_ref)

    assert excinfo.value.error.code is BackendErrorCode.UNSUPPORTED
    assert excinfo.value.error.detail_ns == {"capability": "pause"}
    assert excinfo.value.capability == "pause"


def test_capabilities_without_verification_are_invalid() -> None:
    data = load_sample(SAMPLES_DIR / "capabilities" / "stub-capabilities.json")
    data["capabilities"]["cancel"] = "enforced"
    del data["verification"]["cancel"]
    with pytest.raises(ValueError, match="verification entry"):
        BackendCapabilities.from_dict(data)


async def test_submit_is_idempotent_on_key(backend, tmp_path) -> None:
    backend_obj, expected_issuer, make_req = backend
    first = backend_obj.submit(make_req(tmp_path))
    second = backend_obj.submit(make_req(tmp_path, key="idem-001"))
    assert first.run_ref == second.run_ref
    assert first.received_at == second.received_at
    assert first.run_ref.kind is RefKind.RUN
    assert first.run_ref.issuer_domain == expected_issuer


async def test_same_key_different_content_is_version_conflict(backend, tmp_path) -> None:
    backend_obj, _, make_req = backend
    first_request = make_req(tmp_path)
    backend_obj.submit(first_request)

    conflicting = make_req(tmp_path)
    object.__setattr__(conflicting, "input", {"objective": "Different objective"})
    with pytest.raises(ExecutionBackendError) as excinfo:
        backend_obj.submit(conflicting)

    error = excinfo.value.error
    assert error.code is BackendErrorCode.VERSION_CONFLICT
    assert error.detail_ns["reason"] == "idempotency_key_content_mismatch"


def test_lost_submit_response_is_outcome_unknown_and_reconciles(tmp_path) -> None:
    backend = StubExecutionBackend(unreachable_submit=True)
    request = make_request(tmp_path)

    with pytest.raises(ExecutionBackendError) as excinfo:
        backend.submit(request)

    error = excinfo.value.error
    assert error.code is BackendErrorCode.OUTCOME_UNKNOWN
    assert error.reconciliation is not None
    assert error.reconciliation.strategy == "query_by_idempotency_key"
    assert error.reconciliation.key == request.idempotency_key

    # Reconciliation: same key returns the original receipt - never a second
    # semantically-new execution, and the run is never marked failed.
    receipt = backend.submit(make_request(tmp_path))
    assert receipt.run_ref.issuer_domain == "stub"
    status = backend.get_run(receipt.run_ref)
    assert status.state is not RunState.FAILED


def test_events_are_cursor_resumable_with_explicit_gaps(tmp_path) -> None:
    backend = StubExecutionBackend()
    receipt = backend.submit(make_request(tmp_path))
    run_id = receipt.run_ref.id

    backend.append_event(run_id, "evt-1", {"seq": "first"})
    backend.append_event(run_id, "evt-2", {"seq": "second"})

    page = backend.read_events(receipt.run_ref, cursor=None)
    assert [event.event_id for event in page.events] == ["evt-1", "evt-2"]

    # Resume from the cursor of the first page: no duplicate delivery.
    first_page = backend.read_events(receipt.run_ref, cursor="0")
    assert [event.event_id for event in first_page.events] == ["evt-1", "evt-2"]
    resumed = backend.read_events(receipt.run_ref, cursor="1")
    assert [event.event_id for event in resumed.events] == ["evt-2"]

    backend.inject_gap(run_id, from_sequence=5, to_sequence=6)
    final_page = backend.read_events(receipt.run_ref, cursor="2")
    assert len(final_page.events) == 1
    assert final_page.events[0].kind is EventKind.GAP
    assert final_page.events[0].gap is not None
    assert final_page.events[0].gap.from_sequence == 5
    assert final_page.events[0].gap.to_sequence == 6


def test_cancel_request_is_not_applied(tmp_path) -> None:
    backend = StubExecutionBackend()
    receipt = backend.submit(make_request(tmp_path))

    requested = backend.request_cancel(receipt.run_ref)
    assert requested.state is CancelRequestState.REQUESTED
    assert backend.get_run(receipt.run_ref).state is RunState.RUNNING

    applied = backend.settle_cancel(receipt.run_ref.id)
    assert applied.state is CancelRequestState.APPLIED
    assert backend.get_run(receipt.run_ref).state is RunState.CANCELLED


def test_list_artifacts_returns_input_refs(tmp_path) -> None:
    data = load_sample(SAMPLE_REQUEST)
    data["idempotency_key"] = "idem-arts"
    data["input_artifact_refs"] = [{"kind": "artifact", "issuer_domain": "platform", "id": "art-9"}]
    backend = StubExecutionBackend()
    receipt = backend.submit(ExecutionRequest.from_dict(data))
    artifacts = backend.list_artifacts(receipt.run_ref)
    assert [ref.id for ref in artifacts] == ["art-9"]


def test_contract_version_negotiation_rejects_unsupported_window(tmp_path) -> None:
    """A receiver refusing an out-of-window version maps to version_conflict.

    The stub speaks major version 0; a request speaking an unknown future
    major must not be silently reinterpreted.
    """

    data = load_sample(SAMPLE_REQUEST)
    data["contract_version"] = "99.0"
    request = ExecutionRequest.from_dict(data)
    backend = StubExecutionBackend()

    with pytest.raises(ExecutionBackendError) as excinfo:
        backend.submit(request)

    error = excinfo.value.error
    assert error.code is BackendErrorCode.VERSION_CONFLICT
    assert error.detail_ns["requested"] == "99.0"
    assert error.detail_ns["supported"] == "0.x"
