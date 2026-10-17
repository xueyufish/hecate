"""Durable approval-wait backends for the task dispatch path (step5e).

Three pieces wire the engine's synchronous approval callback to the durable
``WAITING_APPROVAL`` park/wake machinery:

- ``DurableWaitApprovalBackend`` — the no-synchronous-answerer inner backend:
  raises :class:`ApprovalWaitSignal` so the signal escapes the engine loop
  and the dispatcher parks the task. The enclosing
  ``FailingClosedApprovalCallback`` has already emitted the durable
  ``APPROVAL_ASKED`` event; no ``APPROVAL_DECIDED`` follows (no decision
  exists yet).
- ``DurableBindingConsumer`` — the resume-attempt inner backend: consumes the
  approved binding through the ledger's ``consume`` protocol (four-state
  arbitration) when the re-issued call matches the parked context. A
  non-matching protected call is a NEW action: it re-raises the wait signal
  so the task parks again instead of executing unapproved.
- ``mint_pending_approval_binding`` — the park-side authority record:
  judgment ``PENDING`` with digest/subject/scope pinned so the wake gate and
  the consumer validate against the exact parked action.

Principal and scope derivations are shared helpers because the binding check
compares them byte-for-byte between park and consume: the fingerprint must be
derived from the same dispatch identity envelope, and ``task_scope`` must
anchor to the durable task id — stable across attempts — not the engine
session, which changes per attempt.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from hecate_governance.action import target_resources_for
from hecate_governance.approval import (
    ApprovalBinding,
    ApprovalJudgment,
    ApprovalLedger,
    ConsumeStatus,
)
from hecate_governance.principal import ActionPrincipal, PrincipalSource
from hecate_runtime.errors import ApprovalWaitSignal, ExecutionStoppedError
from hecate_runtime.security.approval import approval_arguments_digest
from hecate_runtime.tool_access import ApprovalDecision, ApprovalScope

DEFAULT_PARK_TTL_MINUTES = 60

_WHY_PREFIX = "durable approval wait"


def task_principal_fingerprint(*, actor: str, agent_principal: str | None) -> str:
    """Subject fingerprint for a durable task approval.

    Mirrors the chat-path derivation so both dispatch envelopes speak the
    same principal language: verified platform-entry claims over the actor
    the server resolved, never request-body input.
    """
    return ActionPrincipal.from_verified_claims(
        {"sub": actor},
        source=PrincipalSource.PLATFORM_ENTRY,
        domains=(),
        agent_principal=agent_principal,
    ).fingerprint()


def mint_pending_approval_binding(
    ledger: ApprovalLedger,
    *,
    task_id: uuid.UUID,
    actor: str,
    agent_principal: str | None,
    tool_name: str,
    arguments: dict | None,
    ttl_minutes: int = DEFAULT_PARK_TTL_MINUTES,
) -> tuple[str, ApprovalBinding]:
    """Create the ``PENDING`` authority record for one parked action.

    Returns ``(approval_ref, binding)`` — the ref rides the wait contract,
    the binding is persisted through ``ledger.save`` by the caller. Judgment
    and consumption are separate fields — the decision arrives later through
    the ledger's ``decide``; nothing here authorizes execution.
    """

    issued_at = datetime.now(UTC)
    ref = f"task:{task_id}:{uuid.uuid4()}"
    binding = ApprovalBinding(
        approval_ref=ref,
        principal_fingerprint=task_principal_fingerprint(actor=actor, agent_principal=agent_principal),
        tool_name=tool_name,
        arguments_digest=approval_arguments_digest(arguments),
        target_resources=target_resources_for(arguments or {}),
        domains=(),
        task_scope=str(task_id),
        issued_at=issued_at,
        expires_at=issued_at + timedelta(minutes=ttl_minutes),
        one_time=True,
        judgment=ApprovalJudgment.PENDING,
        reason=_WHY_PREFIX,
        decided_by=None,
    )
    return ref, binding


class DurableWaitApprovalBackend:
    """Inner backend that parks instead of answering.

    The dispatch boundary catches the signal and converts it into a durable
    ``WAITING_APPROVAL`` park; the engine never records a tool denial for
    this path.
    """

    async def request_approval(
        self,
        tool_name: str,
        arguments: dict,
        risk_level: str,
        context: dict,
    ) -> ApprovalDecision:
        """Signal a persistent wait without granting or rejecting the action."""
        raise ApprovalWaitSignal(tool_name, arguments, risk_level, context)


class DurableBindingConsumer:
    """Resume-side inner backend consuming the approved binding at dispatch.

    The wake gate verified the decision; consumption still happens here —
    bound to the concrete action identity — so a binding revoked or expired
    between wake and dispatch cannot activate the action (the ledger's
    ``consume`` re-checks revocation/expiry/params atomically).
    """

    def __init__(
        self,
        ledger: ApprovalLedger,
        *,
        approval_ref: str,
        task_id: uuid.UUID,
        actor: str,
        agent_principal: str | None,
        binding_tool_name: str | None = None,
        binding_arguments_digest: str | None = None,
        binding_invocation_id: str | None = None,
    ) -> None:
        self._ledger = ledger
        self._approval_ref = approval_ref
        self._task_id = task_id
        self._fingerprint = task_principal_fingerprint(actor=actor, agent_principal=agent_principal)
        self._binding_tool = binding_tool_name
        self._binding_digest = binding_arguments_digest
        self._binding_invocation_id = binding_invocation_id

    async def request_approval(
        self,
        tool_name: str,
        arguments: dict,
        risk_level: str,
        context: dict,
    ) -> ApprovalDecision:
        """Consume the original authority for this exact logical action."""
        from hecate_governance.approval import ActionRequest

        digest = approval_arguments_digest(arguments)
        if self._binding_tool is not None and tool_name != self._binding_tool:
            raise ApprovalWaitSignal(tool_name, arguments, risk_level, context)
        if self._binding_digest is not None and digest != self._binding_digest:
            raise ApprovalWaitSignal(tool_name, arguments, risk_level, context)
        invocation_id = str((context or {}).get("invocation_id") or "")
        if not invocation_id:
            raise ExecutionStoppedError("needs_review", "approval requires a stable action identity")
        if self._binding_invocation_id is not None and invocation_id != self._binding_invocation_id:
            raise ApprovalWaitSignal(tool_name, arguments, risk_level, context)
        # Online authority reads re-establish clock trust after a worker restart.
        from hecate_governance.approval import ApprovalGate

        request = ActionRequest(
            invocation_id=invocation_id,
            tool_name=tool_name,
            arguments_digest=digest,
            target_resources=target_resources_for(arguments or {}),
            principal_fingerprint=self._fingerprint,
            task_scope=str(self._task_id),
        )
        verdict = await ApprovalGate(self._ledger).authorize(self._approval_ref, request, now=datetime.now(UTC))
        if verdict.check.value != "ok":
            raise ExecutionStoppedError(
                f"approval_{verdict.check.value}",
                verdict.detail or "approval binding is no longer valid",
                invocation_id,
            )
        try:
            outcome = await self._ledger.consume(self._approval_ref, request, now=datetime.now(UTC))
        except Exception as exc:
            raise ExecutionStoppedError(
                "needs_review", "approval consumption could not be established", invocation_id
            ) from exc
        if outcome.status in (ConsumeStatus.CONSUMED, ConsumeStatus.SAME_ACTION_REBOUND):
            return ApprovalDecision(
                approved=True,
                reason=f"approved for parked action ({outcome.status.value})",
                scope=ApprovalScope.ONCE,
                approval_ref=self._approval_ref,
            )
        if outcome.status is ConsumeStatus.CONFLICT:
            # Already consumed by another action — the parked grant is spent.
            raise ExecutionStoppedError(
                "approval_conflict", "approval already consumed by another action", invocation_id
            )
        raise ExecutionStoppedError(
            f"approval_{outcome.status.value}", "approval invalidated before dispatch", invocation_id
        )
