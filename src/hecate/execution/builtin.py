"""HecateExecutionBackend — the builtin in-process execution backend.

First real implementation of the plan's platform-to-backend contract
(``AgentExecutionBackend``): it drives the shared runtime assembly
(``hecate.runtime.execution_assembly``, plan step5a) instead of duplicating
it, so the platform adapter path and this backend execute through one
assembly. The platform adapter resolves definitions (graph config, session
identity) BEFORE submission; the backend never queries platform ORM — a
request without its resolved ``builtin`` configuration namespace is rejected
explicitly.

Honesty boundaries (plan §一, baseline N1):

- ``request_cancel`` always reports ``requested`` — the engine's interrupt
  is a checkpoint pause, and no platform-level cancel API exists yet (step6).
- Optional interaction capabilities (pause/resume/provide_input/
  resolve_approval/export_context) are declared ``unsupported``.
- A lost or failed schedule reports ``unknown``/``failed`` — never success.

In-process, non-durable, 0.x draft: run records and captured events live in
this instance's memory and are lost on restart; durable run facts and
control-command receipts are step6 scope. Single-event-loop assumption: the
backend schedules execution on the caller's running loop; there is no
internal thread or loop handoff, and submitting with no running loop is an
explicit ``unreachable`` error.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
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
from hecate.contracts.execution.errors import BackendErrorCode
from hecate.contracts.execution.events import EventEnvelope, EventKind
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
from hecate.runtime.execution_assembly import WorkerDependencies, assemble_execution
from hecate.runtime.types import StreamMode

logger = logging.getLogger(__name__)

_PAYLOAD_SCHEMA_REF = "hecate.builtin.engine_event/0"
_EVENT_PAGE_SIZE = 200


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


@dataclass
class _RunRecord:
    """In-memory execution fact for one backend-issued run."""

    request: ExecutionRequest
    digest: str
    run_ref: BackendRef
    receipt: SubmitReceipt | None = None
    state: RunState = RunState.PENDING
    events: list[EventEnvelope] = field(default_factory=list)
    cancel_state: CancelRequestState | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class HecateExecutionBackend(AgentExecutionBackend):
    """Builtin backend: shared-assembly execution behind the six-method contract.

    Constructed with the same engine-injected dependencies as the platform
    service (port, guardrail hooks, policies); ``submit`` expects the
    platform adapter to have resolved the execution definition into
    ``backend_config_ns["builtin"]`` — ``graph_config`` and ``session_id``
    required, optionally ``execution_mode``/``tools``/``kb_ids``/
    ``agent_persona``/``max_iterations``/``agent_id``/``user_id``/
    ``agent_state``/``environment``/``environment_root``.
    """

    def __init__(
        self,
        *,
        port: Any,
        event_store: Any | None = None,
        pre_llm_hook: Any | None = None,
        post_llm_hook: Any | None = None,
        pre_tool_hook: Any | None = None,
        post_tool_hook: Any | None = None,
        middleware_chains: dict | None = None,
        access_policy: Any | None = None,
        approval_callback: Any | None = None,
        tool_policy_rules: list | None = None,
        denial_tracker: Any | None = None,
        suggestion_service: Any | None = None,
        controller_evidence_port: Any | None = None,
        context_chain_factory: Any | None = None,
        citation_provenance: Any | None = None,
        grounding_scoring: dict | None = None,
        session_state_store: Any | None = None,
        context_offload_enabled: bool = False,
        context_offload_threshold_tokens: int | None = None,
        issuer_domain: str = "hecate-builtin",
        supported_contract_major: str = "0",
    ) -> None:
        self._deps = WorkerDependencies(
            port=port,
            pre_llm_hook=pre_llm_hook,
            post_llm_hook=post_llm_hook,
            pre_tool_hook=pre_tool_hook,
            post_tool_hook=post_tool_hook,
            middleware_chains=middleware_chains,
            access_policy=access_policy,
            approval_callback=approval_callback,
            tool_policy_rules=tool_policy_rules,
            event_store=event_store,
            denial_tracker=denial_tracker,
            suggestion_service=suggestion_service,
            controller_evidence_port=controller_evidence_port,
        )
        self._context_chain_factory = context_chain_factory
        self._citation_provenance = citation_provenance
        self._grounding_scoring = grounding_scoring
        self._session_state_store = session_state_store
        self._context_offload_enabled = context_offload_enabled
        self._context_offload_threshold_tokens = context_offload_threshold_tokens
        self._issuer_domain = issuer_domain
        self._supported_major = supported_contract_major
        self._records: dict[str, _RunRecord] = {}

    # --- capability declaration -------------------------------------------------

    def describe_capabilities(self) -> BackendCapabilities:
        """Declare ownership axes and per-capability levels with verification.

        Cancel is ``cooperative`` (a request receipt, never an applied
        guarantee — baseline N1); events resume through opaque sequence
        cursors; tool calls execute through the Hecate gateway wiring of the
        shared assembly. The five interaction capabilities are
        ``unsupported`` in this draft.
        """
        verification = {
            name: CapabilityVerification(
                source="step5a contract tests against the shared runtime assembly",
                checked_at="2026-09-30",
                contract_version="0.1",
                deployment_shape="in-process",
                backend_version="0.1",
            )
            for name in ("cancel", "events_resume", "tool_proxy")
        }
        return BackendCapabilities(
            contract_version="0.1",
            backend_type="hecate-builtin",
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
                controls={
                    "cancel": CapabilityLevel.COOPERATIVE,
                    "events_resume": CapabilityLevel.COOPERATIVE,
                    "tool_proxy": CapabilityLevel.COOPERATIVE,
                },
            ),
            verification=verification,
            extra={
                "durability": "in-process non-durable (step6 adds durable run facts)",
                "event_loop": "single caller loop; no internal thread handoff",
            },
        )

    def _digest(self, request: ExecutionRequest) -> str:
        """Compare request content including its resolved execution definition.

        Compiler-owned copies prevent graph mutation from changing this
        digest. Opaque in-process handles retain repr-based identity; durable
        manifest-based hashing remains step6 scope.
        """
        data = request.to_dict()
        return json.dumps(data, sort_keys=True, default=repr)

    # --- the six methods ----------------------------------------------------------

    def submit(self, request: ExecutionRequest) -> SubmitReceipt:
        """Submit one execution; idempotent on ``idempotency_key``."""
        major = request.contract_version.split(".")[0]
        if major != self._supported_major:
            raise backend_error(
                BackendErrorCode.VERSION_CONFLICT,
                request.run_ref,
                f"contract version {request.contract_version!r} outside supported major {self._supported_major!r}",
                detail_ns={"requested": request.contract_version, "supported": f"{self._supported_major}.x"},
            )
        digest = self._digest(request)
        existing = self._records.get(request.idempotency_key)
        if existing is not None:
            if existing.digest == digest:
                return existing.receipt  # type: ignore[return-value]
            raise backend_error(
                BackendErrorCode.VERSION_CONFLICT,
                request.run_ref,
                f"idempotency_key {request.idempotency_key!r} was already used with different content",
                detail_ns={"reason": "idempotency_key_content_mismatch"},
            )
        cfg = self._require_builtin_config(request)
        # Freeze JSON execution inputs before the scheduled task can observe
        # later caller edits. Environment/state handles stay explicitly local.
        cfg = dict(cfg)
        for key in ("graph_config", "tools", "kb_ids"):
            if key in cfg:
                cfg[key] = deepcopy(cfg[key])
        request = replace(
            request,
            input=deepcopy(request.input),
            backend_config_ns={**request.backend_config_ns, "builtin": cfg},
        )

        run_ref = BackendRef(kind=RefKind.RUN, issuer_domain=self._issuer_domain, id=str(uuid.uuid4()))
        receipt = SubmitReceipt(
            run_ref=run_ref,
            received_at=_now_iso(),
            detail_ns={"idempotency_key": request.idempotency_key},
        )
        record = _RunRecord(request=request, digest=digest, run_ref=run_ref, receipt=receipt)
        self._records[request.idempotency_key] = record

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            del self._records[request.idempotency_key]
            raise backend_error(
                BackendErrorCode.UNREACHABLE,
                request.run_ref,
                "no running event loop: the builtin backend schedules on the caller's loop",
            ) from None
        loop.create_task(self._execute_run(record, cfg))
        return receipt

    def get_run(self, run_ref: BackendRef) -> RunStatus:
        """Return the backend-observed state; scheduling losses read unknown."""
        record = self._find(run_ref)
        detail = dict(record.detail)
        if record.cancel_state is not None:
            detail["cancel_state"] = record.cancel_state.value
        return RunStatus(run_ref=record.run_ref, state=record.state, detail_ns=detail)

    def read_events(self, run_ref: BackendRef, cursor: str | None = None) -> EventPage:
        """Read normalized engine events from a sequence cursor; gaps are explicit."""
        record = self._find(run_ref)
        try:
            start = max(int(cursor) if cursor is not None else 0, 0)
        except ValueError:
            raise backend_error(
                BackendErrorCode.UNSUPPORTED,
                run_ref,
                f"cursor {cursor!r} is not a valid sequence position",
            ) from None
        page = record.events[start : start + _EVENT_PAGE_SIZE]
        next_pos = start + len(page)
        return EventPage(
            events=tuple(page),
            next_cursor=str(next_pos),
            has_more=next_pos < len(record.events),
        )

    def list_artifacts(self, run_ref: BackendRef) -> tuple[BackendRef, ...]:
        """No artifact references yet — the artifact chain lands with 5c/step6."""
        self._find(run_ref)
        return ()

    def request_cancel(self, run_ref: BackendRef) -> CancelReceipt:
        """Record a cancellation request; REQUESTED is never reported as APPLIED."""
        record = self._find(run_ref)
        record.cancel_state = CancelRequestState.REQUESTED
        logger.info("cancel requested (not applied): run %s", run_ref.id)
        return CancelReceipt(
            run_ref=record.run_ref,
            state=CancelRequestState.REQUESTED,
            detail_ns={"note": "engine interrupt is a checkpoint pause; applied cancellation is step6 scope"},
        )

    # --- execution ----------------------------------------------------------------

    def _require_builtin_config(self, request: ExecutionRequest) -> dict[str, Any]:
        if request.budget is not None or request.deadline_at is not None:
            raise backend_error(
                BackendErrorCode.UNSUPPORTED,
                request.run_ref,
                "builtin preview does not enforce resource budgets or deadlines; these require step6 admission",
            )
        cfg = request.backend_config_ns.get("builtin")
        if not isinstance(cfg, dict) or "graph_config" not in cfg or "session_id" not in cfg:
            raise backend_error(
                BackendErrorCode.UNSUPPORTED,
                request.run_ref,
                "builtin backend requires backend_config_ns['builtin'] with pre-resolved "
                "'graph_config' and 'session_id' — resolve definitions in the platform adapter",
            )
        messages = request.input.get("messages") if isinstance(request.input, dict) else None
        if not isinstance(messages, list):
            raise backend_error(
                BackendErrorCode.UNSUPPORTED,
                request.run_ref,
                "request.input['messages'] must be a list",
            )
        return cfg

    async def _execute_run(self, record: _RunRecord, cfg: dict[str, Any]) -> None:
        run_ref = record.run_ref
        request = record.request
        record.state = RunState.RUNNING
        try:
            session_id = uuid.UUID(str(cfg["session_id"]))
            assembled = assemble_execution(
                graph_config=cfg["graph_config"],
                execution_mode=cfg.get("execution_mode", "conversational"),
                messages=list(request.input.get("messages") or []),
                session_id=session_id,
                agent_id=cfg.get("agent_id"),
                user_id=cfg.get("user_id"),
                agent_state=cfg.get("agent_state"),
                tools=cfg.get("tools"),
                kb_ids=cfg.get("kb_ids"),
                agent_persona=cfg.get("agent_persona"),
                environment_root=cfg.get("environment_root"),
                environment=cfg.get("environment"),
                max_iterations=int(cfg.get("max_iterations", 10)),
                deps=self._deps,
                context_chain_factory=self._context_chain_factory,
                citation_provenance=self._citation_provenance,
                grounding_scoring=self._grounding_scoring,
                session_state_store=self._session_state_store,
                context_offload_enabled=self._context_offload_enabled,
                context_offload_threshold_tokens=self._context_offload_threshold_tokens,
            )
            seq = 0
            async for event in assembled.runtime.execute(
                session_id=session_id,
                initial_input=assembled.initial_input,
                stream_mode=StreamMode.VALUES,
                execution_mode=assembled.execution_mode,
            ):
                record.events.append(self._envelope(request, run_ref, seq, event))
                seq += 1
                if isinstance(event, dict) and event.get("type") == "interrupt":
                    record.state = RunState.UNKNOWN
                    record.detail = {"reason": "execution interrupted; continuation controls are unsupported"}
                    return
            record.state = RunState.SUCCEEDED
        except asyncio.CancelledError:
            record.state = RunState.UNKNOWN
            record.detail = {"reason": "schedule cancelled before completion (loop shutdown)"}
            raise
        except Exception as exc:  # noqa: BLE001 — engine failure is a run fact, not a crash
            logger.warning("builtin run %s failed: %s", run_ref.id, exc, exc_info=True)
            record.state = RunState.FAILED
            record.detail = {"reason": str(exc)}

    def _envelope(self, request: ExecutionRequest, run_ref: BackendRef, seq: int, event: Any) -> EventEnvelope:
        now = _now_iso()
        payload = event if isinstance(event, dict) else {"event": str(event)}
        return EventEnvelope(
            contract_version=request.contract_version,
            kind=EventKind.EVENT,
            event_id=f"{run_ref.id}:{seq}",
            task_ref=request.task_ref,
            run_ref=run_ref,
            source_sequence=seq,
            occurred_at=now,
            received_at=now,
            payload_schema_ref=_PAYLOAD_SCHEMA_REF,
            payload=payload,
        )

    def _find(self, run_ref: BackendRef) -> _RunRecord:
        if run_ref.kind is not RefKind.RUN or run_ref.issuer_domain != self._issuer_domain:
            raise backend_error(
                BackendErrorCode.UNSUPPORTED,
                run_ref,
                "reference must be a run issued by this backend",
            )
        for record in self._records.values():
            if record.run_ref.id == run_ref.id:
                return record
        raise backend_error(
            BackendErrorCode.OUTCOME_UNKNOWN,
            run_ref,
            "unknown run reference (records are in-process and non-durable)",
        )
