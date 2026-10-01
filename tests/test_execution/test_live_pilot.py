"""Live-pilot semantics through the test-only HTTP transport.

Covers behaviors whose stub counterparts rely on stub-internal injection
(append_event / inject_gap / settle_cancel) or that are pilot-specific wire
shapes. Runs against the TypeScript pilot; skipped with a stated reason when
no node runtime exists (conditional skip - the A-side vitest suite is the
standing proof).
"""

from __future__ import annotations

import pytest

from hecate.contracts.execution.errors import BackendErrorCode
from hecate.contracts.execution.events import EventKind
from hecate.contracts.execution.references import BackendRef, RefKind
from hecate.contracts.execution.request import ExecutionRequest
from hecate.execution.backend import CancelRequestState, ExecutionBackendError, RunState
from tests.test_execution.conftest import SAMPLES_DIR, load_sample
from tests.test_execution.live_http import LiveHttpBackend, LiveTransportError

SAMPLE_REQUEST = SAMPLES_DIR / "requests" / "submit-minimal.json"


def make_request(overrides: dict[str, object] | None = None, key: str = "idem-live-1") -> ExecutionRequest:
    data = load_sample(SAMPLE_REQUEST)
    data["idempotency_key"] = key
    if overrides:
        data.update(overrides)
    return ExecutionRequest.from_dict(data)


def test_cancel_two_states_through_the_binding(live_backend: LiveHttpBackend) -> None:
    receipt = live_backend.submit(make_request())

    requested = live_backend.request_cancel(receipt.run_ref)
    assert requested.state is CancelRequestState.REQUESTED
    assert live_backend.get_run(receipt.run_ref).state is RunState.RUNNING

    live_backend.advance(receipt.run_ref)
    assert live_backend.get_run(receipt.run_ref).state is RunState.CANCELLED

    page = live_backend.read_events(receipt.run_ref)
    cancelled = [
        event
        for event in page.events
        if event.payload is not None and event.payload.get("reason") == "cancel_requested"
    ]
    assert len(cancelled) == 1


def test_lost_event_range_is_an_explicit_gap_marker(live_backend: LiveHttpBackend) -> None:
    receipt = live_backend.submit(
        make_request(
            {"backend_config_ns": {"pilot": {"simulate_gap": True}}},
            key="idem-live-gap",
        )
    )

    live_backend.advance(receipt.run_ref)
    page = live_backend.read_events(receipt.run_ref)
    gaps = [event for event in page.events if event.kind is EventKind.GAP]
    assert len(gaps) == 1
    assert gaps[0].gap is not None
    assert gaps[0].gap.from_sequence == 2
    assert gaps[0].gap.to_sequence == 2


def test_business_rejection_is_a_result_event_and_the_run_continues(
    live_backend: LiveHttpBackend,
) -> None:
    receipt = live_backend.submit(
        make_request(
            {"backend_config_ns": {"tool": "echo", "parameters": {"text": ""}}},
            key="idem-live-reject",
        )
    )

    live_backend.advance(receipt.run_ref)
    assert live_backend.get_run(receipt.run_ref).state is RunState.SUCCEEDED
    page = live_backend.read_events(receipt.run_ref)
    rejected = [
        event
        for event in page.events
        if event.payload is not None and event.payload.get("status") == "business_rejected"
    ]
    assert len(rejected) == 1
    assert rejected[0].kind is EventKind.EVENT


def test_unsupported_stream_view_is_a_structured_problem(
    live_backend: LiveHttpBackend,
) -> None:
    receipt = live_backend.submit(make_request(key="idem-live-stream"))
    with pytest.raises(ExecutionBackendError) as excinfo:
        live_backend._request("GET", f"/runs/{_wire(receipt.run_ref)}/events:stream")  # noqa: SLF001
    assert excinfo.value.error.code is BackendErrorCode.UNSUPPORTED
    assert excinfo.value.error.detail_ns["capability"] == "events_stream"


def test_version_window_negotiation_maps_to_version_conflict(
    live_backend: LiveHttpBackend,
) -> None:
    with pytest.raises(ExecutionBackendError) as excinfo:
        live_backend.submit(make_request({"contract_version": "9.9"}, key="idem-live-ver"))
    assert excinfo.value.error.code is BackendErrorCode.VERSION_CONFLICT
    assert excinfo.value.error.detail_ns["reason"] == "contract_version_outside_window"


def test_unknown_run_reference_is_a_binding_problem(
    live_backend: LiveHttpBackend,
) -> None:
    unknown = BackendRef(kind=RefKind.RUN, issuer_domain="pilot-ts", id="br-does-not-exist")
    # Finding F1 (pilot report): the binding defines no unknown-run response;
    # the pilot answers with a binding-level 404 that carries no contract code.
    with pytest.raises(LiveTransportError) as excinfo:
        live_backend.get_run(unknown)
    assert "run_not_found" in str(excinfo.value)


def test_artifacts_are_backend_issued_references(live_backend: LiveHttpBackend) -> None:
    receipt = live_backend.submit(make_request(key="idem-live-art"))
    artifacts = live_backend.list_artifacts(receipt.run_ref)
    assert len(artifacts) == 1
    assert artifacts[0].kind is RefKind.ARTIFACT
    assert artifacts[0].issuer_domain == "pilot-ts"


def _wire(run_ref: BackendRef) -> str:
    import urllib.parse

    return urllib.parse.quote(f"{run_ref.issuer_domain}/{run_ref.id}", safe="")


def test_live_tail_cursor_observes_later_events_without_replaying(live_backend: LiveHttpBackend) -> None:
    receipt = live_backend.submit(make_request(key="live-tail-follow"))
    before = live_backend.read_events(receipt.run_ref)
    assert before.next_cursor is not None
    empty = live_backend.read_events(receipt.run_ref, before.next_cursor)
    assert empty.events == ()
    assert empty.next_cursor == before.next_cursor
    live_backend.advance(receipt.run_ref)
    later = live_backend.read_events(receipt.run_ref, empty.next_cursor)
    assert len(later.events) == 1
    assert later.events[0].payload["outcome"] == "succeeded"
    assert not {e.event_id for e in before.events} & {e.event_id for e in later.events}


def test_readonly_callback_crosses_into_python_receiver_once(live_backend: LiveHttpBackend) -> None:
    request = make_request(
        {"backend_config_ns": {"tool": "echo", "parameters": {"text": "callback result"}}}, key="callback-once"
    )
    count = len(live_backend.callback_calls)
    receipt = live_backend.submit(request)
    replay = live_backend.submit(request)
    assert replay == receipt
    assert len(live_backend.callback_calls) == count + 1
    call = live_backend.callback_calls[-1]
    assert call["body"]["platform_run_ref"] == request.run_ref.to_dict()
    assert call["body"]["backend_run_ref"] == receipt.run_ref.to_dict()
    assert call["body"]["trace_correlation"] == request.trace_correlation.to_dict()
    assert call["claims"]["aud"] == "pilot-tool-callback"
    assert call["claims"]["tenant"] == "tenant-1"
    assert call["token"] != live_backend._token  # noqa: SLF001 - regression for credential reuse
    result = live_backend.read_events(receipt.run_ref).events[-1].payload
    assert result["output"] == "callback result"
    assert result["served_by"] == "python-tool-receiver"
