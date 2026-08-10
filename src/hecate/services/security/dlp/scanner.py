"""DLPScanner — three-layer orchestrator combining Registry and Policy Resolver.

Per design.md §D1, the scanner is the orchestration layer that turns raw
text into a fully-enforced :class:`DLPResult`:

1. **Detection**: ask the :class:`DLPRecognizerRegistry` for findings.
2. **Policy**: resolve each finding's action via the
   :class:`DLPPolicyResolver` using the call's ``direction`` and scope.
3. **Enforcement**: pick the most-restrictive action across findings and
   apply it — ``BLOCK`` returns ``text=None``, ``MASK`` replaces matched
   spans with placeholders, ``AUDIT`` and ``ALLOW`` keep the original
   text (audit data is always populated so security teams can review
   detections regardless of action).
"""

from __future__ import annotations

import uuid

from hecate.services.security.dlp.policy import DLPPolicyResolver
from hecate.services.security.dlp.recognizer import DLPRecognizerRegistry
from hecate.services.security.dlp.result import DLPAction, DLPFinding, DLPResult

_DEFAULT_MASK_FORMAT = "[{entity_type}]"


class DLPScanner:
    """Three-layer DLP orchestrator.

    Combines a :class:`DLPRecognizerRegistry` (detection) with a
    :class:`DLPPolicyResolver` (policy) to produce a :class:`DLPResult`.
    """

    def __init__(
        self,
        registry: DLPRecognizerRegistry,
        policy: DLPPolicyResolver,
    ) -> None:
        self._registry = registry
        self._policy = policy

    @property
    def registry(self) -> DLPRecognizerRegistry:
        return self._registry

    @property
    def policy(self) -> DLPPolicyResolver:
        return self._policy

    def scan(
        self,
        text: str,
        direction: str,
        *,
        agent_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
        org_id: uuid.UUID | None = None,
        context: dict[str, object] | None = None,
    ) -> DLPResult:
        """Run the full DLP pipeline and return the enforced result.

        Args:
            text: The text to scan.
            direction: Which trust-boundary direction this scan covers.
                Used by the policy resolver to pick the right rule.
            agent_id: Optional agent scope for policy lookup.
            workspace_id: Optional workspace scope for policy lookup.
            org_id: Optional org scope for policy lookup.
            context: Reserved for future use (e.g., streaming context).

        Returns:
            :class:`DLPResult` with the resolved action, possibly
            masked text, and audit metadata.
        """
        findings = self._registry.analyze(text)
        if not findings:
            return DLPResult(
                findings=[],
                action=DLPAction.ALLOW,
                text=text,
                audit_data=[],
            )

        finding_actions: list[DLPAction] = [
            self._policy.resolve(
                finding.entity_type,
                direction,
                agent_id=agent_id,
                workspace_id=workspace_id,
                org_id=org_id,
            )
            for finding in findings
        ]

        overall_action = DLPAction.overall_action(finding_actions)
        audit_data = [
            {
                "entity_type": finding.entity_type,
                "value": finding.value,
                "start": finding.start,
                "end": finding.end,
                "score": finding.score,
                "recognizer": finding.recognizer,
                "action": action.value,
            }
            for finding, action in zip(findings, finding_actions, strict=True)
        ]

        if overall_action == DLPAction.BLOCK:
            return DLPResult(
                findings=findings,
                action=DLPAction.BLOCK,
                text=None,
                audit_data=audit_data,
            )

        if overall_action == DLPAction.MASK:
            return DLPResult(
                findings=findings,
                action=DLPAction.MASK,
                text=self._apply_masks(text, findings, finding_actions),
                audit_data=audit_data,
            )

        return DLPResult(
            findings=findings,
            action=overall_action,
            text=text,
            audit_data=audit_data,
        )

    @staticmethod
    def _apply_masks(
        text: str,
        findings: list[DLPFinding],
        actions: list[DLPAction],
    ) -> str:
        """Replace ``MASK``-action findings with placeholder text.

        Findings whose action is ``BLOCK``, ``AUDIT``, or ``ALLOW`` are
        left untouched. Iterates findings in reverse ``start`` order so
        character offsets remain valid as the string is mutated.
        """
        mask_indices = [i for i, action in enumerate(actions) if action == DLPAction.MASK]
        if not mask_indices:
            return text
        mask_indices.sort(key=lambda i: findings[i].start, reverse=True)
        result = text
        for i in mask_indices:
            finding = findings[i]
            placeholder = _DEFAULT_MASK_FORMAT.format(entity_type=finding.entity_type)
            result = result[: finding.start] + placeholder + result[finding.end :]
        return result
