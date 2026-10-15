"""Policy profile interface and the non-disableable safety floor.

The profile interface receives *validated parameter content* — not just the
digest. Parameter-conditional rules (dangerous shell flags, forbidden paths
such as ``.env`` writes) evaluate on the actual arguments; the digest exists
only for approval bindings and ledger identity.

The safety floor is applied by :class:`GovernanceService` unconditionally, in
audit, enforced and rollback modes alike: known revocation or policy hard
denial, a policy-required approval without a valid binding, a missing or
unverifiable identity envelope, unwritable evidence and an exhausted metered
budget each stop new protected actions. The floor never widens the approval
requirement — an action the policy explicitly allows without approval does
not trigger the approval branch.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from hecate_governance.action import ActionDecision
from hecate_governance.approval import (
    ActionRequest,
    ApprovalGate,
    ApprovalLedger,
    BindingCheck,
    BindingVerdict,
    ConsumeStatus,
)
from hecate_governance.principal import ActionPrincipal


class BudgetSignal(StrEnum):
    """Budget state as observed by the dispatch boundary."""

    OK = "ok"
    EXHAUSTED = "exhausted"
    RESERVATION_FAILED = "reservation_failed"


@dataclass(frozen=True)
class PolicyContext:
    """Everything one policy evaluation may look at.

    ``arguments`` carries the validated parameter content; ``invocation_id``
    identifies the logical call, ``task_ref`` and ``deployment_id`` bind the
    evaluation to the calling task/deployment so two tasks with different
    grants never share a judgment. ``managed_execution`` is trusted adapter
    provenance (defaults to the stricter managed interpretation); an engine
    explicitly marks local runs. ``approval_ref`` identifies the verified
    action binding for profiles that bridge an expired managed lease.
    """

    principal: ActionPrincipal | None
    tool_name: str
    arguments: Mapping[str, Any]
    arguments_digest: str
    invocation_id: str = ""
    target_resources: tuple[str, ...] = ()
    task_ref: str | None = None
    deployment_id: str | None = None
    metered: bool = False
    budget: BudgetSignal = BudgetSignal.OK
    tool_side_effect: str | None = None
    tool_metadata: Mapping[str, Any] | None = None
    workspace_id: str | None = None
    runtime_rules: tuple[Any, ...] = ()
    managed_execution: bool = True
    approval_ref: str | None = None


@dataclass(frozen=True)
class PolicyOutcome:
    """One profile's judgment."""

    decision: ActionDecision
    policy_version: str
    reason: str = ""
    tool_scope: tuple[str, ...] = ()


class PolicyProfile(ABC):
    """A policy implementation (platform pipeline or runner-local profile)."""

    @property
    @abstractmethod
    def version(self) -> str:
        """Version recorded on decisions for reproducibility."""

    @abstractmethod
    async def evaluate(self, context: PolicyContext) -> PolicyOutcome:
        """Judge one action request; raising fails closed upstream."""


class RevocationView(ABC):
    """Answers whether a principal is currently revoked (known revocations)."""

    @abstractmethod
    async def is_revoked(self, principal: ActionPrincipal) -> bool:
        """Return ``True`` for a known-revoked principal; unknown → ``False``."""


class FloorViolation(StrEnum):
    """Which safety-floor branch stopped the action."""

    IDENTITY_MISSING = "identity_missing"
    EVIDENCE_UNWRITABLE = "evidence_unwritable"
    PRINCIPAL_REVOKED = "principal_revoked"
    POLICY_HARD_DENY = "policy_hard_deny"
    APPROVAL_BINDING_INVALID = "approval_binding_invalid"
    BUDGET_EXHAUSTED = "budget_exhausted"


@dataclass(frozen=True)
class GovernanceDecision:
    """Result of one authorization through the governance service."""

    allowed: bool
    reason: str = ""
    violation: FloorViolation | None = None
    policy_outcome: PolicyOutcome | None = None
    binding: BindingVerdict | None = None


class GovernanceService:
    """Applies the safety floor and one policy profile to action requests.

    The floor runs on every call in every mode — callers cannot switch it off;
    mode selection (audit vs enforced) is a composition concern that decides
    whether the *policy differences* are enforced, never whether the floor is.
    """

    def __init__(
        self,
        profile: PolicyProfile,
        *,
        approval_ledger: ApprovalLedger | None = None,
        revocation: RevocationView | None = None,
    ) -> None:
        self._profile = profile
        self._approvals = ApprovalGate(approval_ledger) if approval_ledger is not None else None
        self._revocation = revocation

    async def authorize(
        self,
        context: PolicyContext,
        *,
        approval_ref: str | None = None,
        now: datetime,
        evidence_writable: bool = True,
    ) -> GovernanceDecision:
        """Authorize one action request; every denial carries its branch."""
        principal = context.principal
        if principal is None or not principal.actor:
            return GovernanceDecision(
                False,
                "identity envelope missing or unverifiable",
                FloorViolation.IDENTITY_MISSING,
            )
        if not evidence_writable:
            return GovernanceDecision(
                False,
                "local evidence storage is not writable",
                FloorViolation.EVIDENCE_UNWRITABLE,
            )
        if self._revocation is not None and await self._revocation.is_revoked(principal):
            return GovernanceDecision(
                False,
                "principal authorization has been revoked",
                FloorViolation.PRINCIPAL_REVOKED,
            )
        if context.metered and context.budget is not BudgetSignal.OK:
            return GovernanceDecision(
                False,
                f"metered budget state is {context.budget.value}",
                FloorViolation.BUDGET_EXHAUSTED,
            )
        try:
            outcome = await self._profile.evaluate(context)
        except Exception as error:  # noqa: BLE001 - fail-closed by contract
            return GovernanceDecision(
                False,
                f"policy evaluation failed: {error}",
                FloorViolation.POLICY_HARD_DENY,
            )
        if outcome.decision is ActionDecision.DENY:
            return GovernanceDecision(
                False,
                outcome.reason or "policy denied the action",
                FloorViolation.POLICY_HARD_DENY,
                outcome,
            )
        if outcome.decision is ActionDecision.REQUIRE_APPROVAL:
            binding = await self._check_approval(context, approval_ref, now)
            allowed = binding.check is BindingCheck.OK
            return GovernanceDecision(
                allowed,
                "" if allowed else f"approval binding invalid: {binding.detail}",
                None if allowed else FloorViolation.APPROVAL_BINDING_INVALID,
                outcome,
                binding,
            )
        # Policy explicitly allows without approval: no approval branch runs.
        return GovernanceDecision(True, outcome.reason, None, outcome)

    async def _check_approval(self, context: PolicyContext, approval_ref: str | None, now: datetime) -> BindingVerdict:
        if self._approvals is None or approval_ref is None:
            return BindingVerdict(BindingCheck.NOT_APPROVED, detail="no approval binding provided")
        principal = context.principal
        if principal is None:
            return BindingVerdict(BindingCheck.NOT_APPROVED, detail="identity envelope missing")
        request = ActionRequest(
            invocation_id=context.invocation_id,
            tool_name=context.tool_name,
            arguments_digest=context.arguments_digest,
            target_resources=context.target_resources,
            principal_fingerprint=principal.fingerprint(),
            deployment_id=context.deployment_id,
            domains=principal.domains,
            task_scope=context.task_ref,
        )
        verdict = await self._approvals.authorize(approval_ref, request, now=now)
        if verdict.check is not BindingCheck.OK:
            return verdict
        consumption = await self._approvals.consume(approval_ref, request, now=now)
        if consumption.status not in (ConsumeStatus.CONSUMED, ConsumeStatus.SAME_ACTION_REBOUND):
            detail = "approval consumed by another action or invalidated"
            if consumption.binding is not None:
                detail += (
                    f" (status={consumption.status.value}, "
                    f"binding_tool={consumption.binding.tool_name!r}, "
                    f"binding_digest={consumption.binding.arguments_digest[:12]})"
                )
            return BindingVerdict(BindingCheck.NOT_APPROVED, detail=detail)
        return verdict
