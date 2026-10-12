"""Platform governance assembly (step7 local slice).

Wires the shared ``GovernanceService`` to platform policy machinery:

- profile: the existing ``ToolPolicyPipeline`` via ``PipelinePolicyProfile``
- revocation: the agent principal lifecycle (``revoked`` stops new actions)
- mode: ``GOVERNANCE_MODE`` setting — ``audit`` (default) logs every judgment
  and leaves approval enforcement to the legacy path, ``enforced`` makes the
  governance decision authoritative, ``off`` is the rollback switch.

The safety floor inside ``GovernanceService`` runs in every mode; the mode
only decides how policy *differences* are treated, never whether the floor
applies. Judgment is per call — no result caching across tasks.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from hecate_governance.action import target_resources_for, tool_arguments_digest
from hecate_governance.claims import (
    ActionClaimLedger,
    ClaimOutcome,
    ClaimStatus,
)
from hecate_governance.delegation import DelegationLink, effective_delegation_domains
from hecate_governance.facts import ToolCandidate, ToolFacts
from hecate_governance.invocation import INVOCATION_NAMESPACE_MODEL, new_invocation_id
from hecate_governance.policy import (
    BudgetSignal,
    FloorViolation,
    GovernanceDecision,
    GovernanceService,
    PolicyContext,
    PolicyProfile,
)
from hecate_governance.principal import ActionPrincipal, PrincipalSource
from hecate_governance.usage import BudgetGate, ReservationStatus, UsageRecord, UsageStatus
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hecate.execution.governance_revocation import PrincipalLifecycleRevocation
from hecate.tools.policy.governance_adapter import PipelinePolicyProfile

logger = logging.getLogger(__name__)


class GovernanceMode(StrEnum):
    """How policy differences are treated at dispatch."""

    AUDIT = "audit"
    ENFORCED = "enforced"
    OFF = "off"


class BudgetRefusedError(RuntimeError):
    """A metered call was refused by the budget reservation."""

    terminal_execution = True


class GovernanceDeniedError(PermissionError):
    """A protected tool call was denied by the governance judgment."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class PlatformGovernance:
    """Composition-owned facade over ``GovernanceService``.

    Builds the verified envelope for platform tool calls (the executing agent
    principal is the server-side identity of the engine path; human
    attribution arrives through the entry identity when provided), evaluates
    per call, and records the decision with the active mode.
    """

    def __init__(
        self,
        service: GovernanceService,
        mode: GovernanceMode = GovernanceMode.AUDIT,
        *,
        facts_sink: Any = None,
        compensations: Any = None,
        action_claims: ActionClaimLedger | None = None,
        model_budget: BudgetGate | None = None,
        identity: dict[str, Any] | None = None,
        workspace_id: Any = None,
    ) -> None:
        self._identity = dict(identity or {})
        self._workspace_id = workspace_id
        self._service = service
        self.mode = mode
        # Observable tool facts go to an injectable sink (tests collect; the
        # default emits a structured log for step10 to export).
        self._facts_sink = facts_sink
        self._compensations = compensations
        self._facts: dict[str, ToolFacts] = {}
        # Claim/receipt authority for ledger-less paths (chat, gateway direct).
        # Ledger-backed paths keep their own ledger as exclusive claim owner.
        self._claims = action_claims
        # step7 4.4: budget gate for metered model calls (call-level ids).
        self._model_budget = model_budget

    def with_identity(self, identity: dict[str, Any], workspace_id: Any = None) -> PlatformGovernance:
        """Bind a verified entry without mutating a facade shared by callers."""
        from copy import copy

        bound = copy(self)
        bound._identity = dict(identity)
        if workspace_id is not None:
            bound._workspace_id = workspace_id
        return bound

    async def authorize_tool_call(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
        identity: dict[str, Any] | None = None,
        invocation_id: str | None = None,
    ) -> GovernanceDecision:
        """Judge one platform tool call (floor + policy, per call)."""
        call_context = dict(context or {})
        call_identity = dict(self._identity if identity is None else identity)
        principal = self.build_principal(call_context, call_identity)
        delegation_reason = self._delegation_check(call_identity, principal)
        if delegation_reason is not None:
            return GovernanceDecision(
                False,
                delegation_reason,
                FloorViolation.POLICY_HARD_DENY,
            )
        compensation_check = self._compensation_check(name, call_context)
        if compensation_check is not None:
            return GovernanceDecision(
                False,
                compensation_check,
                FloorViolation.POLICY_HARD_DENY,
            )
        metered = bool(call_context.get("metered", False))
        gov_context = PolicyContext(
            principal=principal,
            tool_name=name,
            arguments=arguments,
            arguments_digest=tool_arguments_digest(arguments),
            invocation_id=invocation_id or call_context.get("invocation_id") or "",
            target_resources=target_resources_for(arguments),
            task_ref=str(call_context.get("session_id")) if call_context.get("session_id") else None,
            deployment_id=call_identity.get("deployment_id"),
            workspace_id=str(self._workspace_id) if self._workspace_id else None,
            tool_metadata=call_context.get("tool_metadata"),
            runtime_rules=tuple(call_context.get("tool_policy_rules") or ()),
            metered=metered,
            budget=BudgetSignal.EXHAUSTED if call_context.get("budget_exhausted") else BudgetSignal.OK,
        )
        decision = await self._service.authorize(
            gov_context, approval_ref=call_context.get("approval_ref"), now=datetime.now(UTC), evidence_writable=True
        )
        actor = principal.actor
        if self.mode is GovernanceMode.ENFORCED:
            logger.info(
                "governance[enforced] tool=%s actor=%s allowed=%s violation=%s",
                name,
                actor,
                decision.allowed,
                decision.violation.value if decision.violation else None,
            )
        else:
            # Audit (and rollback): every judgment is logged; the floor inside
            # the service already blocked what must not run.
            logger.info(
                "governance[%s] tool=%s actor=%s allowed=%s violation=%s reason=%s",
                self.mode.value,
                name,
                actor,
                decision.allowed,
                decision.violation.value if decision.violation else None,
                decision.reason,
            )
        return decision

    @staticmethod
    def build_principal(call_context: dict[str, Any], call_identity: dict[str, Any]) -> ActionPrincipal:
        """Derive the verified envelope for one call (shared by judge+claim)."""
        actor = call_identity.get("actor") or _default_actor(call_context)
        if not actor:
            return ActionPrincipal(actor="", source=PrincipalSource.PLATFORM_ENTRY)
        return ActionPrincipal.from_verified_claims(
            {"sub": actor},
            source=PrincipalSource.PLATFORM_ENTRY,
            domains=tuple(call_identity.get("data_domains") or ()),
            agent_principal=call_identity.get("agent_principal"),
            on_behalf_of=call_identity.get("on_behalf_of"),
            delegation_id=call_identity.get("delegation_id"),
            deployment_id=call_identity.get("deployment_id"),
        )

    def claim_principal(self, context: dict[str, Any] | None = None) -> ActionPrincipal:
        """The envelope for the claim record (same derivation as judgment)."""
        return self.build_principal(dict(context or {}), self._identity)

    @staticmethod
    def invocation_key(tool_name: str, arguments: dict[str, Any], context: dict | None = None) -> str:
        """Use a caller's stable logical invocation, or allocate a fresh action."""
        import uuid

        return str((context or {}).get("invocation_id") or f"tool:{uuid.uuid4()}")

    def _claim_key(self, name: str, arguments: dict, context: dict) -> str:
        import hashlib

        key = self.invocation_key(name, arguments, context)
        if context.get("ledger_owned"):
            return key
        principal = self.claim_principal(context)
        namespace = f"{self._workspace_id}:{principal.fingerprint()}:{context.get('session_id')}:{key}"
        return f"tool:{hashlib.sha256(namespace.encode()).hexdigest()}"

    async def completed_result(self, name: str, arguments: dict, context: dict) -> tuple[bool, Any]:
        """Read a decided receipt before applying authorization for new effects."""
        from hecate_governance.claims import OutcomeUnknownError

        if self._claims is None or context.get("ledger_owned"):
            return False, None
        if not context.get("invocation_id"):
            raise GovernanceDeniedError("replay requires an invocation_id when action claims are configured")
        key = self._claim_key(name, arguments, context)
        existing = await self._claims.resolve(key)
        if existing is None:
            return False, None
        if (
            existing.tool_name != name
            or existing.arguments_digest != tool_arguments_digest(arguments)
            or existing.principal_fingerprint != self.claim_principal(context).fingerprint()
        ):
            raise GovernanceDeniedError("invocation identity or arguments conflict")
        if existing.completed and existing.succeeded:
            return True, existing.result
        if not existing.completed:
            raise OutcomeUnknownError("invocation already claimed; reconciliation required before retry")
        return False, None

    async def execute_tool(self, registry: Any, name: str, arguments: dict | str, context: dict | None = None) -> Any:
        """Authorize, claim, dispatch and persist one logical tool action."""
        import json as _json

        from hecate_governance.claims import OutcomeUnknownError
        from jsonschema import Draft202012Validator

        if isinstance(arguments, str):
            # Model-emitted arguments arrive as a raw JSON string; normalize
            # BEFORE the digest so the approval binding's arguments_digest
            # (saved from the parsed dict at approval time) matches.
            try:
                arguments = _json.loads(arguments)
            except _json.JSONDecodeError as exc:
                raise GovernanceDeniedError("tool arguments are not valid JSON") from exc
        if not isinstance(arguments, dict):
            raise GovernanceDeniedError("tool arguments must be a JSON object")
        call_context = dict(context or {})
        call_context.setdefault("invocation_id", self.invocation_key(name, arguments, call_context))
        replayed, result = await self.completed_result(name, arguments, call_context)
        if replayed:
            return result
        key = self._claim_key(name, arguments, call_context)
        principal = self.claim_principal(call_context)
        call_context["invocation_id"] = key
        owns_claim = not call_context.get("ledger_owned")
        if hasattr(registry, "describe"):
            call_context["tool_metadata"] = await registry.describe(name)
        metadata = call_context.get("tool_metadata") or {}
        schema = metadata.get("parameters")
        if schema:
            errors = list(Draft202012Validator(schema).iter_errors(arguments))
            if errors:
                raise GovernanceDeniedError(f"input schema invalid: {errors[0].message}")
        decision = await self.authorize_tool_call(name=name, arguments=arguments, context=call_context)
        if not decision.allowed:
            raise GovernanceDeniedError(decision.reason)
        if owns_claim:
            claim = await self.begin_claim(key, tool_name=name, arguments=arguments, principal=principal)
            if claim.status is ClaimStatus.COMPLETED_REPLAY:
                return claim.record.result
            if claim.status is ClaimStatus.CONFLICT:
                raise GovernanceDeniedError(claim.detail or "invocation conflict")
            if claim.status is ClaimStatus.DUPLICATE_IN_FLIGHT:
                raise OutcomeUnknownError(claim.detail or "invocation already claimed")
        # Exceptions after entering the executor do not establish absence of effects.
        try:
            result = await registry.execute(name, arguments, call_context)
        except Exception as exc:
            raise OutcomeUnknownError(f"tool dispatch did not establish an outcome: {exc}") from exc
        output_schema = metadata.get("returns")
        if output_schema and list(Draft202012Validator(output_schema).iter_errors(result)):
            raise OutcomeUnknownError("tool result violates output schema; reconcile the claimed action")
        if owns_claim:
            try:
                await self.finish_claim(key, succeeded=True, result=result)
            except Exception as exc:
                raise OutcomeUnknownError("receipt write failed after successful execution") from exc
        self.record_tool_facts(
            invocation_id=key,
            candidates=[],
            actual={"tool_name": name, "arguments_digest": tool_arguments_digest(arguments)},
            context=call_context,
        )
        return result

    async def begin_claim(
        self,
        key: str,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        principal: ActionPrincipal,
        side_effect_class: str = "unknown",
    ) -> ClaimOutcome:
        """Claim the invocation for execution (at-most-once dispatch)."""
        if self._claims is None:
            return ClaimOutcome(ClaimStatus.CLAIMED, None, "no claim ledger configured")
        return await self._claims.claim(
            key,
            tool_name=tool_name,
            arguments_digest=tool_arguments_digest(arguments),
            principal_fingerprint=principal.fingerprint(),
            side_effect_class=side_effect_class,
        )

    async def finish_claim(
        self,
        key: str,
        *,
        succeeded: bool,
        result: Any = None,
    ) -> None:
        """Attach the terminal receipt; a store failure leaves it unresolved."""
        if self._claims is None:
            return
        import hashlib
        import json as _json

        try:
            result_digest = (
                hashlib.sha256(
                    _json.dumps(result, sort_keys=True, separators=(",", ":"), default=repr).encode("utf-8")
                ).hexdigest()
                if result is not None
                else None
            )
        except Exception:  # noqa: BLE001 — digesting exotic results must not fail the receipt
            result_digest = None
        await self._claims.complete(key, succeeded=succeeded, result=result, result_digest=result_digest)

    async def begin_model_call(self, *, estimated_units: int = 1) -> str | None:
        """Reserve budget for one metered model call; returns its invocation id.

        ``None`` when no model budget gate is wired (unmetered posture). A
        refused reservation raises :class:`BudgetExhaustedError` semantics via
        ``None``-plus-refusal — callers treat a non-reserved outcome as a
        stop, never a silent skip.
        """
        if self._model_budget is None:
            return None
        invocation_id = new_invocation_id(INVOCATION_NAMESPACE_MODEL)
        outcome = await self._model_budget.ensure_budget(invocation_id, estimated_units)
        if outcome.status is not ReservationStatus.RESERVED:
            raise BudgetRefusedError(f"metered model call refused: budget state {outcome.status.value}")
        return invocation_id

    async def settle_model_call(self, invocation_id: str, *, units: int, status: str = "actual") -> None:
        """Settle one model call's usage (idempotent per invocation id)."""
        if self._model_budget is None:
            return
        from hecate_governance.usage import SettleStatus

        outcome = await self._model_budget.settle(UsageRecord(invocation_id, units, UsageStatus(status)))
        if outcome.status is SettleStatus.CONFLICT:
            await self.hold_model_call_unknown(invocation_id)
            raise BudgetRefusedError("model usage settlement failed; reservation held for reconciliation")

    async def hold_model_call_unknown(self, invocation_id: str) -> None:
        """Record an explicit unknown account for an interrupted model call."""
        if self._model_budget is None:
            return
        await self._model_budget.settle(
            UsageRecord(invocation_id, 0, UsageStatus.UNKNOWN, detail="outcome not established")
        )
        await self._model_budget._ledger.hold_for_reconciliation(invocation_id)  # noqa: SLF001

    def record_tool_facts(
        self,
        *,
        invocation_id: str,
        candidates: list[dict[str, str]],
        actual: dict[str, str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> ToolFacts:
        """Record model-proposed candidates and/or the dispatched actual call.

        Candidates arrive from the model's tool-call proposal (one decision
        point); the actual call is recorded at the dispatch boundary with the
        same ``invocation_id`` so the pair stays correlated.
        """
        call_context = dict(context or {})
        key = invocation_id or f"unattributed:{len(self._facts)}"
        facts = self._facts.get(key) or ToolFacts(invocation_id=key)
        updated = facts.model_copy(
            update={
                "candidates": tuple(ToolCandidate(**c) for c in candidates) if candidates else facts.candidates,
                "actual": ToolCandidate(**actual) if actual else facts.actual,
                "agent_principal": call_context.get("agent_principal") or facts.agent_principal,
                "session_ref": str(call_context.get("session_id"))
                if call_context.get("session_id") is not None
                else facts.session_ref,
            }
        )
        self._facts[key] = updated
        payload = updated.model_dump(mode="json")
        if self._facts_sink is not None:
            self._facts_sink(payload)
        else:
            logger.info("governance.tool_facts %s", payload)
        return updated

    def _delegation_check(self, identity: dict[str, Any], principal: ActionPrincipal) -> str | None:
        """Intersect the delegation chain with the request; deny by default.

        The grant domains ride the verified identity (server-side derivation),
        never the request body. A denial carries the full chain for tracing.
        """
        delegation_id = identity.get("delegation_id")
        if not delegation_id:
            return None
        grant_domains = tuple(identity.get("delegation_domains") or ())
        link = DelegationLink(
            delegation_id=str(delegation_id),
            grantor=str(identity.get("delegation_grantor") or "unknown"),
            grantee=principal.actor,
            domains=grant_domains,
        )
        requested = principal.domains or tuple(identity.get("requested_domains") or ())
        decision = effective_delegation_domains((link,), requested, now=datetime.now(UTC))
        if not decision.allowed:
            return f"delegation denied: {decision.reason}"
        return None

    def _compensation_check(self, name: str, call_context: dict[str, Any]) -> str | None:
        """Only declared and approved compensation pairs may compensate."""
        compensates = call_context.get("compensates_action_id")
        if compensates is None:
            return None
        compensates_tool = str(call_context.get("compensates_tool") or "")
        if self._compensations is not None:
            declaration = self._compensations.approved_declaration(name, compensates_tool)
            if declaration is not None:
                return None
        return (
            f"tool {name!r} is not a declared, approved compensation for "
            f"{compensates_tool!r}; compensation operations must be explicitly declared"
        )


class GovernedToolRegistry:
    """Route a direct tool loop through the same authority as RuntimePort."""

    def __init__(self, registry: Any, governance: PlatformGovernance) -> None:
        self._registry = registry
        self._governance = governance

    async def completed_result(self, name: str, args: dict, context: dict) -> tuple[bool, Any]:
        """Expose receipt recovery to a direct loop before its legacy guards."""
        return await self._governance.completed_result(name, args, context)

    async def execute(self, name: str, args: dict, context: dict | None = None) -> Any:
        """Execute one governed action through the underlying registry."""
        return await self._governance.execute_tool(self._registry, name, args, context)


class GovernedModelService:
    """Meter direct chat model calls using the shared budget authority."""

    def __init__(self, service: Any, governance: PlatformGovernance) -> None:
        self._service = service
        self._governance = governance

    async def chat(self, **kwargs: Any) -> Any:
        """Reserve and settle one non-streaming provider call."""
        from hecate.core.composition.runtime_port_adapter import _estimate_usage

        messages = kwargs.get("messages") or []
        key = await self._governance.begin_model_call(
            estimated_units=_estimate_usage(messages, "")["total_tokens"] + int(kwargs.get("max_tokens") or 4096)
        )
        try:
            result = await self._service.chat(**kwargs)
            if key:
                usage = result.usage or _estimate_usage(messages, result.content or "")
                await self._governance.settle_model_call(
                    key,
                    units=int(
                        usage.get("total_tokens") or (usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0))
                    ),
                    status="estimated" if usage.get("estimated") else "actual",
                )
            return result
        except BaseException:
            if key:
                await self._governance.hold_model_call_unknown(key)
            raise

    async def chat_stream(self, **kwargs: Any):
        """Keep an interrupted stream's reservation until usage is reconciled."""
        from hecate.core.composition.runtime_port_adapter import _estimate_usage

        messages = kwargs.get("messages") or []
        key = await self._governance.begin_model_call(
            estimated_units=_estimate_usage(messages, "")["total_tokens"] + int(kwargs.get("max_tokens") or 4096)
        )
        parts = []
        usage = None
        try:
            async for chunk in self._service.chat_stream(**kwargs):
                if chunk.get("usage"):
                    usage = chunk["usage"]
                parts.append(chunk.get("content") or "")
                yield chunk
            if key:
                resolved = usage or _estimate_usage(messages, "".join(parts))
                await self._governance.settle_model_call(
                    key,
                    units=int(
                        resolved.get("total_tokens")
                        or (resolved.get("prompt_tokens", 0) + resolved.get("completion_tokens", 0))
                    ),
                    status="estimated" if resolved.get("estimated") else "actual",
                )
        except BaseException:
            if key:
                await self._governance.hold_model_call_unknown(key)
            raise


def build_platform_governance(
    db: AsyncSession | None,
    *,
    agent_id: Any = None,
    profile: PolicyProfile | None = None,
    mode: GovernanceMode | None = None,
    rules: list[dict[str, Any]] | None = None,
    identity: dict[str, Any] | None = None,
    workspace_id: Any = None,
) -> PlatformGovernance:
    """Assemble the platform governance facade (composition entry point)."""
    from hecate.core.config import settings

    resolved_mode = mode or GovernanceMode(settings.GOVERNANCE_MODE)
    if identity is None and agent_id is not None:
        identity = {"actor": f"agent:{agent_id}", "agent_principal": str(agent_id)}
    # Persistent authority: approval bindings and claims ride the platform
    # database when a session is available (tests without a db fall back to
    # in-memory injection by the caller).
    approval_ledger = None
    action_claims = None
    if db is not None and getattr(db, "bind", None) is not None:
        # Stub/test sessions without a bound engine cannot host the SQL
        # authority; composition callers inject in-memory ledgers instead.
        from hecate.enterprise.governance_ledgers import (
            SqlActionClaimLedger,
            SqlApprovalLedger,
        )

        factory = async_sessionmaker(db.bind, expire_on_commit=False)
        approval_ledger = SqlApprovalLedger(db, session_factory=factory)
        action_claims = SqlActionClaimLedger(db, session_factory=factory)
    service = GovernanceService(
        profile or PipelinePolicyProfile(rules=rules),
        approval_ledger=approval_ledger,
        revocation=PrincipalLifecycleRevocation(db, agent_id),
    )
    model_budget = None
    if db is not None and workspace_id is not None and settings.QUOTA_ENABLED:
        from hecate.ops.budget_reservations import QuotaBudgetLedger

        model_budget = BudgetGate(
            QuotaBudgetLedger(db, workspace_id=workspace_id, session_factory=factory, unit_scale=0.00001)
        )
    return PlatformGovernance(
        service,
        resolved_mode,
        action_claims=action_claims,
        model_budget=model_budget,
        identity=identity,
        workspace_id=workspace_id,
    )


def _default_actor(call_context: dict[str, Any]) -> str:
    agent_id = call_context.get("agent_id")
    if agent_id:
        return f"agent:{agent_id}"
    return ""
