"""Approval binding: the authority record and its consume protocol.

An approval binds its judgment to the exact execution context it authorizes:
principal (via envelope fingerprint), tool, arguments digest, target
resources, deployment, data domains and task scope. Judgment state
(pending/approved/rejected) and consumption state are separate fields; a
one-time consumption atomically claims the unique authority record by setting
``consumed_by_invocation_id`` (conditions: approved, not expired, not revoked,
placeholder empty). Other actions are rejected with an explicit conflict; the
same action recovering before dispatch re-enters on its existing consumption
association.

The :class:`ApprovalLedger` seam owns the authority records; the SQL-backed
implementations (platform and runner-local) arrive with their storage tasks —
this module ships the reference in-memory ledger and the fail-closed gate
shared by all dispatch paths.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from hecate_governance.clock import PROCESS_EPOCH, TimeAnchor, TrustedClock


class ApprovalJudgment(StrEnum):
    """Judgment state of one approval record."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class BindingMismatch(StrEnum):
    """Which binding dimension disagreed with the incoming request."""

    PRINCIPAL = "principal"
    TOOL = "tool"
    ARGUMENTS = "arguments"
    RESOURCES = "resources"
    DEPLOYMENT = "deployment"
    DOMAINS = "domains"
    TASK_SCOPE = "task_scope"


class BindingCheck(StrEnum):
    """Result of validating a request against a binding."""

    OK = "ok"
    MISMATCH = "mismatch"
    NOT_APPROVED = "not_approved"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ConsumeStatus(StrEnum):
    """Outcome of consuming one approval for a specific action."""

    CONSUMED = "consumed"
    SAME_ACTION_REBOUND = "same_action_rebound"
    CONFLICT = "conflict"
    NOT_APPROVED = "not_approved"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ApprovalBinding(BaseModel):
    """The unique authority record for one approval.

    Keyed by ``approval_ref``; judgment and consumption are separate fields.
    ``issued_at`` is the wall-clock anchor recorded at issuance time — trusted
    time validation (clock rollback / untrustworthy restart) is layered on top
    by the dispatch paths, never relaxed here.
    """

    model_config = ConfigDict(frozen=True)

    approval_ref: str
    principal_fingerprint: str
    tool_name: str
    arguments_digest: str
    target_resources: tuple[str, ...] = Field(default=())
    deployment_id: str | None = None
    domains: tuple[str, ...] = Field(default=())
    task_scope: str | None = None
    issued_at: datetime
    expires_at: datetime | None = None
    one_time: bool = True
    judgment: ApprovalJudgment = ApprovalJudgment.PENDING
    reason: str = ""
    decided_by: str | None = None
    revoked: bool = False
    consumed_by_invocation_id: str | None = None
    anchor_wall: datetime = Field(default_factory=lambda: datetime.now(UTC))
    anchor_monotonic: float = Field(default_factory=time.monotonic)
    clock_epoch: str = Field(default_factory=lambda: PROCESS_EPOCH)


@dataclass(frozen=True)
class ActionRequest:
    """The execution context asking for authorization/consumption."""

    invocation_id: str
    tool_name: str
    arguments_digest: str
    target_resources: tuple[str, ...]
    principal_fingerprint: str
    deployment_id: str | None = None
    domains: tuple[str, ...] = ()
    task_scope: str | None = None


@dataclass(frozen=True)
class BindingVerdict:
    """Binding validation result with the mismatch detail when present."""

    check: BindingCheck
    mismatch: BindingMismatch | None = None
    detail: str = ""


@dataclass(frozen=True)
class ConsumeOutcome:
    """Consumption result; ``binding`` carries the post-consume record."""

    status: ConsumeStatus
    binding: ApprovalBinding | None = None


def check_binding(
    binding: ApprovalBinding,
    request: ActionRequest,
    *,
    now: datetime,
) -> BindingVerdict:
    """Validate ``request`` against ``binding`` (pure check, no state change).

    A consumed placeholder does not invalidate the binding for *its own*
    action: the same ``invocation_id`` recovering before actual dispatch may
    continue on the existing association. Other actions must not reuse it —
    consumption arbitration happens in :meth:`ApprovalLedger.consume`.
    """
    if binding.revoked:
        return BindingVerdict(BindingCheck.REVOKED, detail="approval has been revoked")
    if binding.judgment is not ApprovalJudgment.APPROVED:
        return BindingVerdict(BindingCheck.NOT_APPROVED, detail=f"judgment is {binding.judgment.value}")
    if binding.expires_at is not None and now >= binding.expires_at:
        return BindingVerdict(BindingCheck.EXPIRED, detail="approval has expired")
    if binding.principal_fingerprint != request.principal_fingerprint:
        import os

        if os.environ.get("HECATE_DEBUG_BINDING"):
            print(
                f"[BINDING-DEBUG] binding_fp={binding.principal_fingerprint[:16]} "
                f"request_fp={request.principal_fingerprint[:16]}"
            )
        return BindingVerdict(BindingCheck.MISMATCH, BindingMismatch.PRINCIPAL, "principal differs")
    if binding.tool_name != request.tool_name:
        return BindingVerdict(BindingCheck.MISMATCH, BindingMismatch.TOOL, "tool differs")
    if binding.arguments_digest != request.arguments_digest:
        return BindingVerdict(BindingCheck.MISMATCH, BindingMismatch.ARGUMENTS, "parameters changed")
    if not set(request.target_resources).issubset(set(binding.target_resources)):
        return BindingVerdict(BindingCheck.MISMATCH, BindingMismatch.RESOURCES, "target resources differ")
    if binding.deployment_id != request.deployment_id:
        return BindingVerdict(BindingCheck.MISMATCH, BindingMismatch.DEPLOYMENT, "deployment differs")
    if not set(request.domains).issubset(set(binding.domains)):
        return BindingVerdict(BindingCheck.MISMATCH, BindingMismatch.DOMAINS, "data domains differ")
    if binding.task_scope != request.task_scope:
        return BindingVerdict(BindingCheck.MISMATCH, BindingMismatch.TASK_SCOPE, "task scope differs")
    return BindingVerdict(BindingCheck.OK)


class ApprovalLedger(ABC):
    """Authority store for approval bindings.

    Implementations must make :meth:`consume` atomic on the unique authority
    record — the SQL-backed stores do it with a conditional update on
    ``consumed_by_invocation_id``; no per-action record may carry the
    arbitration.
    """

    @abstractmethod
    async def save(self, binding: ApprovalBinding) -> None:
        """Create a new binding; ``approval_ref`` must be unique."""

    @abstractmethod
    async def get(self, approval_ref: str) -> ApprovalBinding | None:
        """Return the binding or ``None`` when unknown."""

    @abstractmethod
    async def decide(
        self, approval_ref: str, *, approved: bool, reason: str, decided_by: str | None
    ) -> ApprovalBinding:
        """Transition judgment once from pending to approved/rejected."""

    @abstractmethod
    async def revoke(self, approval_ref: str) -> ApprovalBinding:
        """Mark the binding revoked; revocation covers existing approvals."""

    @abstractmethod
    async def consume(self, approval_ref: str, request: ActionRequest, *, now: datetime) -> ConsumeOutcome:
        """Atomically claim the record for ``request.invocation_id``."""


class MemoryApprovalLedger(ApprovalLedger):
    """Reference in-memory ledger for tests and local tooling.

    Concurrency is serialized per ``approval_ref`` so the one-time claim is a
    true compare-and-set within this process. Production stores are SQL-backed
    (platform and runner-local durable storage).
    """

    def __init__(self) -> None:
        self._records: dict[str, ApprovalBinding] = {}
        self._fail_mode: Exception | None = None

    def fail_next_access(self, error: Exception) -> None:
        """Inject a storage failure for the next access (fail-closed tests)."""
        self._fail_mode = error

    async def save(self, binding: ApprovalBinding) -> None:
        self._maybe_fail()
        if binding.approval_ref in self._records:
            raise ValueError(f"approval_ref {binding.approval_ref!r} already exists")
        self._records[binding.approval_ref] = binding

    async def get(self, approval_ref: str) -> ApprovalBinding | None:
        self._maybe_fail()
        return self._records.get(approval_ref)

    async def decide(
        self, approval_ref: str, *, approved: bool, reason: str, decided_by: str | None
    ) -> ApprovalBinding:
        self._maybe_fail()
        binding = self._records[approval_ref]
        if binding.judgment is not ApprovalJudgment.PENDING:
            raise ValueError(f"approval {approval_ref!r} already decided")
        updated = binding.model_copy(
            update={
                "judgment": ApprovalJudgment.APPROVED if approved else ApprovalJudgment.REJECTED,
                "reason": reason,
                "decided_by": decided_by,
            }
        )
        self._records[approval_ref] = updated
        return updated

    async def revoke(self, approval_ref: str) -> ApprovalBinding:
        self._maybe_fail()
        binding = self._records[approval_ref]
        updated = binding.model_copy(update={"revoked": True})
        self._records[approval_ref] = updated
        return updated

    async def consume(self, approval_ref: str, request: ActionRequest, *, now: datetime) -> ConsumeOutcome:
        self._maybe_fail()
        binding = self._records[approval_ref]
        verdict = check_binding(binding, request, now=now)
        if verdict.check is BindingCheck.REVOKED:
            return ConsumeOutcome(ConsumeStatus.REVOKED, binding)
        if verdict.check in (BindingCheck.NOT_APPROVED, BindingCheck.EXPIRED):
            status = ConsumeStatus.NOT_APPROVED if verdict.check is BindingCheck.NOT_APPROVED else ConsumeStatus.EXPIRED
            return ConsumeOutcome(status, binding)
        if verdict.check is BindingCheck.MISMATCH:
            # Binding mismatch means this request is not the one the approval
            # was written for: never consume, surface the dimension.
            return ConsumeOutcome(ConsumeStatus.CONFLICT, binding)
        if binding.consumed_by_invocation_id is not None:
            if binding.consumed_by_invocation_id == request.invocation_id:
                return ConsumeOutcome(ConsumeStatus.SAME_ACTION_REBOUND, binding)
            return ConsumeOutcome(ConsumeStatus.CONFLICT, binding)
        if binding.one_time:
            updated = binding.model_copy(update={"consumed_by_invocation_id": request.invocation_id})
            self._records[approval_ref] = updated
            return ConsumeOutcome(ConsumeStatus.CONSUMED, updated)
        return ConsumeOutcome(ConsumeStatus.CONSUMED, binding)

    def _maybe_fail(self) -> None:
        if self._fail_mode is not None:
            error, self._fail_mode = self._fail_mode, None
            raise error


class ApprovalGate:
    """Fail-closed facade over the ledger for dispatch paths.

    Every ledger access problem (missing record, storage failure, malformed
    state) becomes a denial — never a silent pass. Binding mismatches surface
    their dimension so callers can trigger re-judgment (e.g. a fresh approval
    request) instead of a bare refusal.
    """

    def __init__(self, ledger: ApprovalLedger) -> None:
        self._ledger = ledger

    async def authorize(
        self,
        approval_ref: str,
        request: ActionRequest,
        *,
        now: datetime,
        clock: Any = None,
        clock_anchor: Any = None,
    ) -> BindingVerdict:
        """Validate the binding for dispatch; fail-closed on any error.

        With a trusted clock and anchor, an expiry-dependent binding (one
        that carries ``expires_at``) is rejected when the time cannot be
        trusted — clock rollback or a restarted process. Bindings without an
        expiry are not expiry-dependent and are unaffected.
        """
        try:
            binding = await self._ledger.get(approval_ref)
            if binding is None:
                return BindingVerdict(BindingCheck.NOT_APPROVED, detail="unknown approval reference")
            if binding.expires_at is not None:
                resolved_clock = clock or TrustedClock()
                persisted_anchor = TimeAnchor(binding.anchor_wall, binding.anchor_monotonic, binding.clock_epoch)
                _, untrusted_reason = resolved_clock.trusted_now(clock_anchor or persisted_anchor)
                if untrusted_reason is not None and binding.expires_at is not None:
                    return BindingVerdict(BindingCheck.EXPIRED, detail=f"time cannot be trusted: {untrusted_reason}")
            return check_binding(binding, request, now=now)
        except Exception as error:  # noqa: BLE001 - fail-closed by contract
            return BindingVerdict(BindingCheck.NOT_APPROVED, detail=f"approval evaluation failed: {error}")

    async def consume(
        self,
        approval_ref: str,
        request: ActionRequest,
        *,
        now: datetime,
        clock: Any = None,
        clock_anchor: Any = None,
    ) -> ConsumeOutcome:
        """Claim the record for this action; fail-closed on any error.

        A storage failure yields an explicit conflict outcome — the caller must
        not dispatch the protected action, and no consumption state was changed.
        Trusted-clock semantics mirror :meth:`authorize`.
        """
        verdict = await self.authorize(approval_ref, request, now=now, clock=clock, clock_anchor=clock_anchor)
        if verdict.check is not BindingCheck.OK:
            status = {
                BindingCheck.EXPIRED: ConsumeStatus.EXPIRED,
                BindingCheck.REVOKED: ConsumeStatus.REVOKED,
                BindingCheck.NOT_APPROVED: ConsumeStatus.NOT_APPROVED,
            }.get(verdict.check, ConsumeStatus.CONFLICT)
            if verdict.detail.startswith("approval evaluation failed:"):
                status = ConsumeStatus.CONFLICT
            return ConsumeOutcome(status, None)
        try:
            return await self._ledger.consume(approval_ref, request, now=now)
        except Exception as error:  # noqa: BLE001 - fail-closed by contract
            return ConsumeOutcome(ConsumeStatus.CONFLICT, _failed_binding(approval_ref, str(error)))


def _failed_binding(approval_ref: str, detail: str) -> ApprovalBinding:
    """Placeholder carrying the failure reason for structured denial evidence."""
    return ApprovalBinding(
        approval_ref=approval_ref,
        principal_fingerprint="",
        tool_name="",
        arguments_digest="",
        issued_at=datetime.min.replace(tzinfo=UTC),
        reason=detail,
    )
