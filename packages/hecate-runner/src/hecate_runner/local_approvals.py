"""Local approval authority for the standalone host (step7, task 5.4).

The host owns its approval facts: only principals registered in the profile's
``governance.approvers`` may sign an approval; the binding (subject
fingerprint, parameter digest, expiry) persists in durable storage and
survives restarts; one-time consumption claims the record atomically. The
gate wired into the engine validates a wake grant's approval reference
against this authority — a bare token never authorizes a protected dispatch.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from hecate_durable.storage.models import LocalApprovalRow
from hecate_governance.approval import (
    ActionRequest,
    ApprovalBinding,
    ApprovalGate,
    ApprovalJudgment,
    ApprovalLedger,
    BindingCheck,
    ConsumeOutcome,
    ConsumeStatus,
    check_binding,
)
from hecate_governance.clock import TimeAnchor, TrustedClock
from hecate_governance.principal import ActionPrincipal, PrincipalSource
from sqlalchemy import or_, select, update


def _binding_from_row(row: LocalApprovalRow) -> ApprovalBinding:
    def _aware(value: str | None) -> datetime | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    return ApprovalBinding(
        approval_ref=row.approval_ref,
        principal_fingerprint=row.principal_fingerprint,
        tool_name=row.tool_name,
        arguments_digest=row.arguments_digest,
        target_resources=tuple(row.target_resources or ()),
        issued_at=_aware(row.issued_at) or datetime.now(UTC),
        expires_at=_aware(row.expires_at),
        one_time=row.one_time,
        judgment=ApprovalJudgment(row.judgment),
        decided_by=row.decided_by,
        revoked=row.revoked,
        consumed_by_invocation_id=row.consumed_by_invocation_id,
        domains=tuple(row.domains or ()),
        task_scope=row.task_ref,
        deployment_id=row.deployment_id,
        reason=row.reason or "",
        anchor_wall=_aware(row.anchor_wall) or _aware(row.issued_at),
        anchor_monotonic=row.anchor_monotonic or 0.0,
        clock_epoch=row.clock_epoch or "legacy-untrusted",
    )


class DurableLocalApprovalLedger(ApprovalLedger):
    """Authority over ``durable_local_approvals`` (sync-backed, async API)."""

    def __init__(self, session_factory, *, approvers: Any = None) -> None:  # noqa: ANN001
        self._session_factory = session_factory
        self._approvers = approvers

    def _record_binding(self, binding: ApprovalBinding) -> None:
        with self._session_factory() as session:
            exists = session.execute(
                select(LocalApprovalRow.id).where(LocalApprovalRow.approval_ref == binding.approval_ref)
            ).scalar_one_or_none()
            if exists is not None:
                raise ValueError(f"approval_ref {binding.approval_ref!r} already exists")
            session.add(
                LocalApprovalRow(
                    approval_ref=binding.approval_ref,
                    principal_fingerprint=binding.principal_fingerprint,
                    tool_name=binding.tool_name,
                    arguments_digest=binding.arguments_digest,
                    target_resources=list(binding.target_resources),
                    issued_at=binding.issued_at.isoformat(),
                    expires_at=binding.expires_at.astimezone(UTC).isoformat() if binding.expires_at else None,
                    one_time=binding.one_time,
                    judgment=binding.judgment.value,
                    decided_by=binding.decided_by or "",
                    revoked=binding.revoked,
                    domains=list(binding.domains),
                    task_ref=binding.task_scope,
                    deployment_id=binding.deployment_id,
                    reason=binding.reason,
                    anchor_wall=binding.anchor_wall.isoformat(),
                    anchor_monotonic=binding.anchor_monotonic,
                    clock_epoch=binding.clock_epoch,
                )
            )
            session.commit()

    async def save(self, binding: ApprovalBinding) -> None:
        self._record_binding(binding)

    async def get(self, approval_ref: str) -> ApprovalBinding | None:
        with self._session_factory() as session:
            row = session.execute(
                select(LocalApprovalRow).where(LocalApprovalRow.approval_ref == approval_ref)
            ).scalar_one_or_none()
            if row is None:
                return None
            registered = self._approvers() if callable(self._approvers) else self._approvers
            if registered is not None and row.decided_by not in registered:
                return None
            return _binding_from_row(row)

    async def decide(
        self, approval_ref: str, *, approved: bool, reason: str, decided_by: str | None
    ) -> ApprovalBinding:
        raise NotImplementedError("local approvals are signed at creation time")

    async def revoke(self, approval_ref: str) -> ApprovalBinding:
        with self._session_factory() as session:
            row = session.execute(
                select(LocalApprovalRow).where(LocalApprovalRow.approval_ref == approval_ref)
            ).scalar_one_or_none()
            if row is None:
                raise KeyError(f"unknown approval_ref {approval_ref!r}")
            row.revoked = True
            session.commit()
            return _binding_from_row(row)

    async def consume(self, approval_ref: str, request: ActionRequest, *, now: datetime) -> ConsumeOutcome:
        with self._session_factory() as session:
            row = session.execute(
                select(LocalApprovalRow).where(LocalApprovalRow.approval_ref == approval_ref)
            ).scalar_one_or_none()
            if row is None:
                return ConsumeOutcome(
                    ConsumeStatus.NOT_APPROVED,
                    None,
                )
            binding = _binding_from_row(row)
            verdict = check_binding(binding, request, now=now)
            if verdict.check is BindingCheck.REVOKED:
                return ConsumeOutcome(
                    ConsumeStatus.REVOKED,
                    binding,
                )
            if verdict.check in (BindingCheck.NOT_APPROVED, BindingCheck.EXPIRED):
                status = (
                    ConsumeStatus.NOT_APPROVED if verdict.check is BindingCheck.NOT_APPROVED else ConsumeStatus.EXPIRED
                )
                return ConsumeOutcome(status, binding)
            if verdict.check is BindingCheck.MISMATCH:
                return ConsumeOutcome(
                    ConsumeStatus.CONFLICT,
                    binding,
                )
            if row.consumed_by_invocation_id is not None:
                status = (
                    ConsumeStatus.SAME_ACTION_REBOUND
                    if row.consumed_by_invocation_id == request.invocation_id
                    else ConsumeStatus.CONFLICT
                )
                return ConsumeOutcome(status, binding)
            if not binding.one_time:
                return ConsumeOutcome(
                    ConsumeStatus.CONSUMED,
                    binding,
                )
            changed = session.execute(
                update(LocalApprovalRow)
                .where(
                    LocalApprovalRow.id == row.id,
                    LocalApprovalRow.consumed_by_invocation_id.is_(None),
                    LocalApprovalRow.revoked.is_(False),
                    LocalApprovalRow.judgment == "approved",
                    or_(
                        LocalApprovalRow.expires_at.is_(None),
                        LocalApprovalRow.expires_at > now.astimezone(UTC).isoformat(),
                    ),
                )
                .values(consumed_by_invocation_id=request.invocation_id)
            ).rowcount
            session.commit()
            session.refresh(row)
            if not changed:
                status = (
                    ConsumeStatus.SAME_ACTION_REBOUND
                    if row.consumed_by_invocation_id == request.invocation_id
                    else ConsumeStatus.CONFLICT
                )
                return ConsumeOutcome(status, _binding_from_row(row))
            return ConsumeOutcome(
                ConsumeStatus.CONSUMED,
                _binding_from_row(row),
            )


def subject_fingerprint_for_run(principal: str, domains: tuple[str, ...]) -> str:
    """The dispatch-side subject fingerprint (principal + domain scope)."""
    return ActionPrincipal.from_verified_claims(
        {"sub": principal}, source=PrincipalSource.RUNNER_LOCAL, domains=domains
    ).fingerprint()


def make_local_approval_finder(
    session_factory,
    *,
    approvers: Any,
) -> Callable[[ActionRequest], Any]:
    """Build the engine's approval locator over the local authority (step7 I-Cb).

    Returns ``async def find(request) -> approval_ref | None``: the ref of
    an unconsumed, unrevoked, decided-approved binding matching the request
    (subject fingerprint, tool, parameter digest). The locator only
    prefilters — the approval gate remains the authority that validates the
    binding under trusted time and consumes it atomically.
    """

    async def _find(request: ActionRequest) -> str | None:
        with session_factory() as session:
            row = session.execute(
                select(LocalApprovalRow)
                .where(
                    LocalApprovalRow.principal_fingerprint == request.principal_fingerprint,
                    LocalApprovalRow.tool_name == request.tool_name,
                    LocalApprovalRow.arguments_digest == request.arguments_digest,
                    LocalApprovalRow.revoked.is_(False),
                    LocalApprovalRow.judgment == "approved",
                    LocalApprovalRow.consumed_by_invocation_id.is_(None),
                )
                .order_by(LocalApprovalRow.issued_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                return None
            registered = approvers() if callable(approvers) else approvers
            if registered is not None and row.decided_by not in registered:
                return None
            return row.approval_ref

    return _find


def make_local_approval_gate(
    session_factory,
    *,
    approvers: Any,
    clock: TrustedClock | None = None,
    anchor: TimeAnchor | None = None,
) -> Callable[[str, ActionRequest], Any]:
    """Build the engine's approval gate over the actual verified action request.

    The gate validates the reference against the local authority — including
    the approver registration and trusted time — and consumes it for the
    dispatch. Refusals never consume.
    """
    ledger = DurableLocalApprovalLedger(session_factory, approvers=approvers)
    gate = ApprovalGate(ledger)

    async def _gate(approval_ref: str, request: ActionRequest) -> bool:
        binding = await ledger.get(approval_ref)
        if binding is None:
            return False
        registered = approvers() if callable(approvers) else approvers
        if binding.decided_by not in registered:
            return False  # unregistered signer: the approval is invalid
        outcome = await gate.consume(
            approval_ref,
            request,
            now=datetime.now(UTC),
            clock=clock,
            clock_anchor=anchor,
        )
        return outcome.status in (ConsumeStatus.CONSUMED, ConsumeStatus.SAME_ACTION_REBOUND)

    return _gate
