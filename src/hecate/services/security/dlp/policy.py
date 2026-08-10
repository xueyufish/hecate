"""DLP policy resolver — maps ``(entity_type, direction)`` to a :class:`DLPAction`.

Implements the four-level scope lookup (agent > workspace > org > default)
from design.md §D4, with wildcard support and ``is_locked`` enforcement.

``is_locked`` semantics: a rule with ``is_locked=True`` cannot be overridden
by rules at more specific scopes. So an org-level ``BLOCK`` marked locked
prevents workspace and agent scopes from downgrading it to ``ALLOW`` or
``AUDIT``. Within the same scope level, more specific (less-wildcard)
matches still win over wildcard matches.

Wildcards:
* ``entity_type="*"`` matches any entity type.
* ``direction="*"`` matches any direction.

When multiple rules at different scopes match, the most specific scope
wins by default. If a less-specific matching rule is ``is_locked``, the
locked rule wins instead. No matching rule returns :attr:`DLPAction.ALLOW`
(the fail-open default from design.md §D5).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import IntEnum

from hecate.services.security.dlp.result import DLPAction


class PolicyScope(IntEnum):
    """Scope rank — lower number = more specific scope."""

    AGENT = 0
    WORKSPACE = 1
    ORG = 2
    DEFAULT = 3


@dataclass(frozen=True)
class DLPPolicyRule:
    """A single policy rule mapping ``entity_type`` + ``direction`` to an action.

    Attributes:
        entity_type: Canonical entity name, or ``"*"`` to match any.
        direction: One of ``"llm_input"``, ``"llm_output"``, ``"tool_input"``,
            ``"tool_output"``, ``"mcp_response"``; or ``"*"`` to match any.
        action: Enforcement decision (:class:`DLPAction`).
        scope: Which scope level this rule applies at.
        scope_id: Identifier within the scope (``agent_id``, ``workspace_id``,
            or ``org_id`` as UUIDs). ``None`` for ``DEFAULT`` scope.
        is_locked: When ``True``, this rule overrides more specific scopes
            that would otherwise win (design.md §D4).
        mask_format: Format string for ``MASK`` actions. ``None`` means use
            the default ``"[ENTITY_TYPE]"`` placeholder.
    """

    entity_type: str
    direction: str
    action: DLPAction
    scope: PolicyScope
    scope_id: uuid.UUID | None = None
    is_locked: bool = False
    mask_format: str | None = None


class DLPPolicyResolver:
    """Resolve a ``(entity_type, direction)`` lookup to a :class:`DLPAction`."""

    def __init__(self, rules: list[DLPPolicyRule]) -> None:
        self._rules = list(rules)

    @property
    def rules(self) -> list[DLPPolicyRule]:
        return list(self._rules)

    def resolve(
        self,
        entity_type: str,
        direction: str,
        *,
        agent_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
        org_id: uuid.UUID | None = None,
    ) -> DLPAction:
        """Return the most specific applicable rule's action, or ALLOW."""
        candidates: list[tuple[DLPPolicyRule, int]] = []
        for rule in self._rules:
            if not self._scope_applies(rule, agent_id, workspace_id, org_id):
                continue
            entity_exact = rule.entity_type == entity_type
            direction_exact = rule.direction == direction
            entity_ok = entity_exact or rule.entity_type == "*"
            direction_ok = direction_exact or rule.direction == "*"
            if not (entity_ok and direction_ok):
                continue
            specificity = (2 if entity_exact else 0) + (1 if direction_exact else 0)
            candidates.append((rule, specificity))

        if not candidates:
            return DLPAction.ALLOW

        candidates.sort(key=lambda pair: (pair[0].scope, -pair[1]))
        top_rule, _ = candidates[0]

        for candidate, _ in candidates[1:]:
            if candidate.scope > top_rule.scope and candidate.is_locked:
                return candidate.action

        return top_rule.action

    @staticmethod
    def _scope_applies(
        rule: DLPPolicyRule,
        agent_id: uuid.UUID | None,
        workspace_id: uuid.UUID | None,
        org_id: uuid.UUID | None,
    ) -> bool:
        """Return ``True`` if ``rule``'s scope covers the call context."""
        if rule.scope == PolicyScope.AGENT:
            return rule.scope_id == agent_id
        if rule.scope == PolicyScope.WORKSPACE:
            if workspace_id is None:
                return False
            return rule.scope_id == workspace_id
        if rule.scope == PolicyScope.ORG:
            if org_id is None:
                return False
            return rule.scope_id == org_id
        return True
