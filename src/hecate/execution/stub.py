"""Deterministic in-memory execution backend for contract tests.

``StubExecutionBackend`` is the second implementation required before the
``AgentExecutionBackend`` extension point may exist (the named consumers are
the non-Python pilot and the step5a builtin wrapper). It permanently declares
``pause``/``resume``/``export_context`` as ``unsupported`` - the standing
negative case that unsupported capabilities must surface structured
UNSUPPORTED errors, never fabricated success.

Fault injection mirrors the scenario-pack stub conventions (no mocking
frameworks): ``unreachable_submit`` turns submit into outcome_unknown AFTER
recording the receipt, so the reconciliation path (resubmitting the same
idempotency key returns the original receipt) is exercisable.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from hecate.contracts.execution.capabilities import (
    BackendCapabilities,
    CapabilityLevel,
    CapabilitySet,
    CapabilityVerification,
    EnvironmentOwner,
    HarnessOwner,
    OwnershipAxes,
    ToolExecutionPoint,
)
from hecate.contracts.execution.errors import BackendErrorCode, Reconciliation
from hecate.contracts.execution.events import EventEnvelope, EventKind, GapRange
from hecate.contracts.execution.references import BackendRef, RefKind
from hecate.contracts.execution.request import ExecutionRequest
from hecate.execution.backend import (
    AgentExecutionBackend,
    CancelReceipt,
    CancelRequestState,
    EventPage,
    RunState,
    RunStatus,
    SubmitReceipt,
    backend_error,
)


@dataclass
class StubExecutionBackend(AgentExecutionBackend):
    """Second implementation of the backend contract; contract tests run against it."""

    issuer_domain: str = "stub"
    supported_contract_major: str = "0"
    unreachable_submit: bool = False
    _receipts: dict[str, tuple[ExecutionRequest, SubmitReceipt]] = field(default_factory=dict)
    _runs: dict[str, RunStatus] = field(default_factory=dict)
    _events: dict[str, list[EventEnvelope]] = field(default_factory=dict)
    _artifacts: dict[str, list[BackendRef]] = field(default_factory=dict)
    _cancels: dict[str, CancelReceipt] = field(default_factory=dict)
    _sequence: dict[str, int] = field(default_factory=dict)

    def describe_capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            contract_version="0.1",
            backend_type="stub",
            ownership=OwnershipAxes(
                harness=HarnessOwner.HECATE,
                environment=EnvironmentOwner.HECATE,
                tool_execution=ToolExecutionPoint.HECATE_GATEWAY,
            ),
            capabilities=CapabilitySet(
                provide_input=CapabilityLevel.UNSUPPORTED,
                resolve_approval=CapabilityLevel.UNSUPPORTED,
                pause=CapabilityLevel.UNSUPPORTED,
                resume=CapabilityLevel.UNSUPPORTED,
                export_context=CapabilityLevel.UNSUPPORTED,
                controls={"cancel": CapabilityLevel.COOPERATIVE, "events_resume": CapabilityLevel.COOPERATIVE},
            ),
            verification={
                name: CapabilityVerification(
                    source="stub contract tests",
                    checked_at="2026-10-01T00:00:00Z",
                    deployment_shape="in_memory_test",
                    observation_source="backend_reported",
                )
                for name in ("cancel", "events_resume")
            },
        )

    def submit(self, request: ExecutionRequest) -> SubmitReceipt:
        requested_major = request.contract_version.split(".")[0]
        if requested_major != self.supported_contract_major:
            raise backend_error(
                BackendErrorCode.VERSION_CONFLICT,
                request.run_ref,
                "contract version outside support window",
                detail_ns={
                    "requested": request.contract_version,
                    "supported": f"{self.supported_contract_major}.x",
                },
            )

        existing = self._receipts.get(request.idempotency_key)
        if existing is not None:
            prior_request, receipt = existing
            if prior_request != request:
                raise backend_error(
                    BackendErrorCode.VERSION_CONFLICT,
                    request.run_ref,
                    "idempotency key bound to a different request content",
                    detail_ns={
                        "reason": "idempotency_key_content_mismatch",
                        "idempotency_key": request.idempotency_key,
                    },
                )
            return receipt

        # The backend owns its IDs; two callers may use the same local run ID.
        run_id = f"br-{len(self._receipts) + 1}"
        receipt = SubmitReceipt(
            run_ref=BackendRef(RefKind.RUN, self.issuer_domain, run_id),
            received_at="2026-09-30T10:00:00Z",
        )
        # Record before any fault injection: a lost response still left the
        # run behind, so same-key resubmission reconciles instead of doubling.
        self._receipts[request.idempotency_key] = (deepcopy(request), receipt)
        self._runs[run_id] = RunStatus(run_ref=receipt.run_ref, state=RunState.RUNNING)
        self._events[run_id] = []
        self._artifacts[run_id] = list(request.input_artifact_refs)
        self._sequence[run_id] = 0

        if self.unreachable_submit:
            raise backend_error(
                BackendErrorCode.OUTCOME_UNKNOWN,
                request.run_ref,
                "connection lost after submit; execution state unknown",
                reconciliation=Reconciliation(strategy="query_by_idempotency_key", key=request.idempotency_key),
            )
        return receipt

    def get_run(self, run_ref: BackendRef) -> RunStatus:
        require_run(run_ref, self.issuer_domain)
        return self._runs[run_ref.id]

    def read_events(self, run_ref: BackendRef, cursor: str | None = None) -> EventPage:
        require_run(run_ref, self.issuer_domain)
        events = self._events[run_ref.id]
        start = int(cursor) if cursor else 0
        window = tuple(events[start:])
        has_more = False
        return EventPage(events=window, next_cursor=str(start + len(window)), has_more=has_more)

    def list_artifacts(self, run_ref: BackendRef) -> tuple[BackendRef, ...]:
        require_run(run_ref, self.issuer_domain)
        return tuple(self._artifacts[run_ref.id])

    def request_cancel(self, run_ref: BackendRef) -> CancelReceipt:
        require_run(run_ref, self.issuer_domain)
        if self.get_run(run_ref).state is RunState.CANCELLED:
            return self._cancels[run_ref.id]
        receipt = CancelReceipt(run_ref=run_ref, state=CancelRequestState.REQUESTED)
        self._cancels[run_ref.id] = receipt
        return receipt

    # --- test helpers (not part of the contract surface) ---

    def append_event(self, run_id: str, event_id: str, payload: dict[str, Any]) -> EventEnvelope:
        self._sequence[run_id] += 1
        event = EventEnvelope(
            contract_version="0.1",
            kind=EventKind.EVENT,
            event_id=event_id,
            task_ref=BackendRef(RefKind.TASK, "platform", "t-stub"),
            run_ref=BackendRef(RefKind.RUN, self.issuer_domain, run_id),
            source_sequence=self._sequence[run_id],
            occurred_at="2026-09-30T10:00:00Z",
            received_at="2026-09-30T10:00:00Z",
            payload_schema_ref="https://hecate.dev/contracts/execution/0.1/event-payloads/generic.json",
            payload=payload,
        )
        self._events[run_id].append(event)
        return event

    def inject_gap(self, run_id: str, from_sequence: int, to_sequence: int) -> EventEnvelope:
        self._sequence[run_id] = to_sequence + 1
        gap_event = EventEnvelope(
            contract_version="0.1",
            kind=EventKind.GAP,
            event_id=f"gap-{from_sequence}-{to_sequence}",
            task_ref=BackendRef(RefKind.TASK, "platform", "t-stub"),
            run_ref=BackendRef(RefKind.RUN, self.issuer_domain, run_id),
            source_sequence=to_sequence + 1,
            occurred_at="2026-09-30T10:00:01Z",
            received_at="2026-09-30T10:00:01Z",
            gap=GapRange(from_sequence=from_sequence, to_sequence=to_sequence),
        )
        self._events[run_id].append(gap_event)
        return gap_event

    def settle_cancel(self, run_id: str) -> CancelReceipt:
        applied = CancelReceipt(
            run_ref=BackendRef(RefKind.RUN, self.issuer_domain, run_id),
            state=CancelRequestState.APPLIED,
        )
        self._cancels[run_id] = applied
        self._runs[run_id] = RunStatus(run_ref=applied.run_ref, state=RunState.CANCELLED)
        return applied


def require_run(run_ref: BackendRef, issuer_domain: str) -> None:
    if run_ref.kind is not RefKind.RUN:
        raise ValueError(f"reference kind mismatch: expected run, got {run_ref.kind.value}")
    if run_ref.issuer_domain != issuer_domain:
        raise ValueError("backend run reference issuer mismatch")
