"""Local policy profile for the standalone host (step7, tasks 5.1-5.3).

Implements the shared governance ``PolicyProfile`` seam over the runner's
local authorization config — no platform IAM, no organization directory, no
approval UI. Everything is derived from the local trust material and the
profile's ``governance`` block:

- tool-level allow rules with data-domain scoping (default deny);
- per-principal local revocation, effective on the next dispatch;
- the protected-write enablement gate (5.5): disabled by default, the
  experimental tier requires the business-API attestation.
"""

from __future__ import annotations

from typing import Any

from hecate_governance.action import ActionDecision
from hecate_governance.policy import PolicyContext, PolicyOutcome, PolicyProfile, RevocationView
from hecate_governance.principal import ActionPrincipal


class LocalPolicyProfile(PolicyProfile):
    """Fail-closed local authorization over the profile's governance rules."""

    def __init__(self, governance: Any) -> None:
        # ``governance`` is the profile's GovernanceLocalConfig (duck-typed to
        # avoid a profile import cycle in tests).
        self._source = governance
        governance = governance() if callable(governance) else governance
        self._rules = tuple(getattr(governance, "rules", ()))
        self._write_enablement = str(getattr(governance, "write_enablement", "disabled"))
        self._write_attestation = getattr(governance, "write_attestation", None)

    @property
    def version(self) -> str:
        return "runner-local-1"

    async def evaluate(self, context: PolicyContext) -> PolicyOutcome:
        if callable(self._source):
            current = self._source()
            self._rules = tuple(current.rules)
            self._write_enablement = current.write_enablement
            self._write_attestation = current.write_attestation
        if context.principal is None:
            return PolicyOutcome(ActionDecision.DENY, self.version, "identity missing")
        write_classes = {"idempotent_write", "non_idempotent_write", "external_side_effect"}
        if context.tool_side_effect in write_classes:
            return self._evaluate_write(context)
        rule = self._match_rule(context)
        if rule is None:
            return PolicyOutcome(ActionDecision.DENY, self.version, "tool not locally authorized")
        if rule["approval_required"]:
            return PolicyOutcome(ActionDecision.REQUIRE_APPROVAL, self.version, "local rule requires approval")
        return PolicyOutcome(ActionDecision.ALLOW, self.version, "locally authorized")

    def _evaluate_write(self, context: PolicyContext) -> PolicyOutcome:
        """Protected-write enablement gate (5.5).

        ``disabled`` (default) refuses every protected write — no attestation,
        no enablement. ``experimental`` is the lower governance tier: it only
        covers actions whose target business API independently enforces final
        authorization, which the attestation declares.
        """
        if self._write_enablement == "experimental" and self._write_attestation:
            rule = self._match_rule(context)
            if rule is None:
                return PolicyOutcome(ActionDecision.DENY, self.version, "tool not locally authorized")
            if rule["approval_required"]:
                return PolicyOutcome(ActionDecision.REQUIRE_APPROVAL, self.version, "local rule requires approval")
            return PolicyOutcome(
                ActionDecision.ALLOW,
                self.version,
                f"experimental tier; attestation: {self._write_attestation}",
            )
        return PolicyOutcome(
            ActionDecision.DENY,
            self.version,
            "protected writes are not enabled on this profile (bypass closure unverified)",
        )

    def _match_rule(self, context: PolicyContext) -> dict | None:
        principal_domains = set(context.principal.domains) if context.principal else set()
        for rule in self._rules:
            if rule["tool"] != context.tool_name:
                continue
            rule_domains = set(rule["domains"])
            requested = context.arguments.get("domain")
            if requested is not None:
                if requested not in rule_domains or requested not in principal_domains:
                    continue
            elif not rule_domains.issubset(principal_domains):
                continue
            return rule
        return None


class LocalRevocationView(RevocationView):
    """Known-revocation view over the profile's local revocation list."""

    def __init__(self, governance: Any) -> None:
        self._source = governance
        governance = governance() if callable(governance) else governance
        self._revoked = tuple(getattr(governance, "revoked_principals", ()))

    async def is_revoked(self, principal: ActionPrincipal) -> bool:
        revoked = self._source().revoked_principals if callable(self._source) else self._revoked
        return principal.actor in revoked
