"""Authority-backed approval callback for the chat path (step7, task 3.2).

Extends the fail-closed chat callback so every human approval creates a
durable authority binding (subject fingerprint, parameter digest, optional
expiry, one-time flag) in the approval ledger — the same record the dispatch
boundary's approval branch validates against. The digest-pinned SESSION cache
from the base class still governs in-session replays; the authority record is
what other entry points and the reconciliation path consult.

Cross-subject reuse is refused structurally: the binding stores the granting
subject's fingerprint, and a dispatch by a different principal fails the
binding check (principal mismatch) — a same-parameters approval is not
transferable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from hecate_governance.action import target_resources_for, tool_arguments_digest
from hecate_governance.approval import ApprovalBinding, ApprovalJudgment, ApprovalLedger
from hecate_governance.principal import ActionPrincipal, PrincipalSource

from hecate.runtime.security.approval import (
    FailingClosedApprovalCallback,
    approval_arguments_digest,
)
from hecate.runtime.tool_access import ApprovalDecision

DEFAULT_BINDING_TTL_MINUTES = 60


def chat_subject_fingerprint(
    *, session_id: uuid.UUID | None, workspace_id: uuid.UUID | None, agent_id: uuid.UUID | None
) -> str:
    """Stable subject fingerprint for a chat approval.

    The granting subject of a chat approval is the execution context the
    server verified (session within workspace, resolved agent); request-body
    claims never reach this derivation.
    """
    return ActionPrincipal.from_verified_claims(
        {"sub": f"agent:{agent_id}" if agent_id else ""},
        source=PrincipalSource.PLATFORM_ENTRY,
        domains=(),
        agent_principal=str(agent_id) if agent_id else None,
    ).fingerprint()


class AuthorityBackedApprovalCallback(FailingClosedApprovalCallback):
    """Chat approval callback that persists an authority binding per grant."""

    def __init__(
        self,
        *,
        authority: ApprovalLedger,
        binding_ttl_minutes: int = DEFAULT_BINDING_TTL_MINUTES,
        identity: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._identity = dict(
            identity
            or {
                "actor": f"agent:{self.agent_id}" if self.agent_id else "",
                "agent_principal": str(self.agent_id) if self.agent_id else None,
            }
        )
        self._references: dict[str, str] = {}
        self._authority = authority
        self._binding_ttl = timedelta(minutes=binding_ttl_minutes)

    def _reference_key(self, tool_name: str, arguments: dict, context: dict) -> str:
        call_id = context.get("tool_call_id") or arguments.get("tool_call_id", "")
        return f"{tool_name}:{tool_arguments_digest(arguments)}:{call_id}"

    async def request_approval(
        self, tool_name: str, arguments: dict, risk_level: str, context: dict
    ) -> ApprovalDecision:
        """Return the durable authority reference alongside the human decision."""
        decision = await super().request_approval(tool_name, arguments, risk_level, context)
        if decision.approved:
            decision.approval_ref = self._references.get(self._reference_key(tool_name, arguments, context))
        return decision

    async def _delegate_inner(
        self,
        tool_name: str,
        arguments: dict,
        risk_level: str,
        context: dict,
    ) -> ApprovalDecision:
        decision = await super()._delegate_inner(tool_name, arguments, risk_level, context)
        if not decision.approved:
            return decision
        approval_ref = f"chat:{self.session_id}:{uuid.uuid4()}"
        issued_at = datetime.now(UTC)
        expires_raw = context.get("approval_expires_at")
        expires_at = expires_raw if isinstance(expires_raw, datetime) else issued_at + self._binding_ttl
        await self._authority.save(
            ApprovalBinding(
                approval_ref=approval_ref,
                principal_fingerprint=ActionPrincipal.from_verified_claims(
                    {"sub": self._identity.get("actor", "")},
                    source=PrincipalSource.PLATFORM_ENTRY,
                    domains=tuple(self._identity.get("data_domains") or ()),
                    agent_principal=self._identity.get("agent_principal"),
                    on_behalf_of=self._identity.get("on_behalf_of"),
                    delegation_id=self._identity.get("delegation_id"),
                    deployment_id=self._identity.get("deployment_id"),
                ).fingerprint(),
                tool_name=tool_name,
                arguments_digest=approval_arguments_digest(arguments),
                target_resources=target_resources_for(arguments),
                domains=tuple(self._identity.get("data_domains") or ()),
                task_scope=str(self.session_id) if self.session_id else None,
                deployment_id=self._identity.get("deployment_id"),
                issued_at=issued_at,
                expires_at=expires_at,
                one_time=decision.scope.value == "once",
                judgment=ApprovalJudgment.APPROVED,
                reason=decision.reason,
                decided_by=str(self._identity.get("actor") or ""),
            )
        )
        self._references[self._reference_key(tool_name, arguments, context)] = approval_ref
        decision.approval_ref = approval_ref
        return decision


def arguments_digest_for_chat(arguments: dict | None) -> str:
    """Digest over the chat tool arguments minus the call handle.

    Matches the runtime scope-cache digest so the authority binding and the
    in-session cache speak the same parameter identity.
    """
    from hecate.runtime.security.approval import approval_arguments_digest

    return approval_arguments_digest(arguments)
