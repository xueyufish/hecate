"""Tests for DLPPolicyResolver.

Covers spec §dlp-policy-management requirements:
* Four-level scope lookup (agent > workspace > org > default).
* is_locked enforcement — locked rules at less-specific scopes override
  more-specific non-locked rules.
* Wildcards for entity_type and direction.
* No matching rule returns ALLOW (fail-open default).
"""

from __future__ import annotations

from hecate.services.security.dlp.policy import (
    DLPPolicyResolver,
    DLPPolicyRule,
    PolicyScope,
)
from hecate.services.security.dlp.result import DLPAction


def rule(
    entity_type: str = "EMAIL",
    direction: str = "llm_output",
    action: DLPAction = DLPAction.MASK,
    scope: PolicyScope = PolicyScope.DEFAULT,
    scope_id: str | None = None,
    is_locked: bool = False,
    mask_format: str | None = None,
) -> DLPPolicyRule:
    return DLPPolicyRule(
        entity_type=entity_type,
        direction=direction,
        action=action,
        scope=scope,
        scope_id=scope_id,
        is_locked=is_locked,
        mask_format=mask_format,
    )


class TestPolicyResolverDefaults:
    def test_no_rules_returns_allow(self) -> None:
        resolver = DLPPolicyResolver([])
        assert resolver.resolve("EMAIL", "llm_output") == DLPAction.ALLOW

    def test_no_matching_rule_returns_allow(self) -> None:
        resolver = DLPPolicyResolver([rule(entity_type="SSN", action=DLPAction.BLOCK)])
        assert resolver.resolve("EMAIL", "llm_output") == DLPAction.ALLOW

    def test_default_rule_matches_any_context(self) -> None:
        resolver = DLPPolicyResolver(
            [
                rule(
                    entity_type="*",
                    direction="*",
                    scope=PolicyScope.DEFAULT,
                    action=DLPAction.AUDIT,
                )
            ]
        )
        assert resolver.resolve("EMAIL", "llm_output") == DLPAction.AUDIT
        assert resolver.resolve("SSN", "tool_input") == DLPAction.AUDIT
        assert (
            resolver.resolve(
                "EMAIL",
                "llm_output",
                agent_id="a1",
                workspace_id="w1",
                org_id="o1",
            )
            == DLPAction.AUDIT
        )


class TestPolicyResolverScopePrecedence:
    def test_agent_over_workspace(self) -> None:
        resolver = DLPPolicyResolver(
            [
                rule(
                    scope=PolicyScope.AGENT,
                    scope_id="a1",
                    action=DLPAction.BLOCK,
                ),
                rule(
                    scope=PolicyScope.WORKSPACE,
                    scope_id="w1",
                    action=DLPAction.AUDIT,
                ),
            ]
        )
        assert resolver.resolve("EMAIL", "llm_output", agent_id="a1", workspace_id="w1") == DLPAction.BLOCK

    def test_workspace_over_org(self) -> None:
        resolver = DLPPolicyResolver(
            [
                rule(
                    scope=PolicyScope.WORKSPACE,
                    scope_id="w1",
                    action=DLPAction.BLOCK,
                ),
                rule(
                    scope=PolicyScope.ORG,
                    scope_id="o1",
                    action=DLPAction.AUDIT,
                ),
            ]
        )
        assert resolver.resolve("EMAIL", "llm_output", workspace_id="w1", org_id="o1") == DLPAction.BLOCK

    def test_org_over_default(self) -> None:
        resolver = DLPPolicyResolver(
            [
                rule(scope=PolicyScope.ORG, scope_id="o1", action=DLPAction.BLOCK),
                rule(scope=PolicyScope.DEFAULT, action=DLPAction.AUDIT),
            ]
        )
        assert resolver.resolve("EMAIL", "llm_output", org_id="o1") == DLPAction.BLOCK

    def test_agent_over_default(self) -> None:
        resolver = DLPPolicyResolver(
            [
                rule(
                    scope=PolicyScope.AGENT,
                    scope_id="a1",
                    action=DLPAction.BLOCK,
                ),
                rule(scope=PolicyScope.DEFAULT, action=DLPAction.AUDIT),
            ]
        )
        assert resolver.resolve("EMAIL", "llm_output", agent_id="a1") == DLPAction.BLOCK


class TestPolicyResolverIsLocked:
    def test_locked_org_blocks_workspace_override(self) -> None:
        resolver = DLPPolicyResolver(
            [
                rule(
                    scope=PolicyScope.WORKSPACE,
                    scope_id="w1",
                    action=DLPAction.AUDIT,
                ),
                rule(
                    scope=PolicyScope.ORG,
                    scope_id="o1",
                    action=DLPAction.BLOCK,
                    is_locked=True,
                ),
            ]
        )
        assert resolver.resolve("EMAIL", "llm_output", workspace_id="w1", org_id="o1") == DLPAction.BLOCK

    def test_locked_workspace_blocks_agent_override(self) -> None:
        resolver = DLPPolicyResolver(
            [
                rule(
                    scope=PolicyScope.AGENT,
                    scope_id="a1",
                    action=DLPAction.AUDIT,
                ),
                rule(
                    scope=PolicyScope.WORKSPACE,
                    scope_id="w1",
                    action=DLPAction.BLOCK,
                    is_locked=True,
                ),
            ]
        )
        assert resolver.resolve("EMAIL", "llm_output", agent_id="a1", workspace_id="w1") == DLPAction.BLOCK

    def test_unlocked_workspace_does_not_block(self) -> None:
        resolver = DLPPolicyResolver(
            [
                rule(
                    scope=PolicyScope.AGENT,
                    scope_id="a1",
                    action=DLPAction.BLOCK,
                ),
                rule(
                    scope=PolicyScope.WORKSPACE,
                    scope_id="w1",
                    action=DLPAction.AUDIT,
                ),
            ]
        )
        assert resolver.resolve("EMAIL", "llm_output", agent_id="a1", workspace_id="w1") == DLPAction.BLOCK

    def test_locked_at_same_scope_does_not_change_outcome(self) -> None:
        resolver = DLPPolicyResolver(
            [rule(scope=PolicyScope.AGENT, scope_id="a1", action=DLPAction.BLOCK, is_locked=True)]
        )
        assert resolver.resolve("EMAIL", "llm_output", agent_id="a1") == DLPAction.BLOCK


class TestPolicyResolverWildcards:
    def test_wildcard_entity_matches_any_entity(self) -> None:
        resolver = DLPPolicyResolver([rule(entity_type="*", action=DLPAction.MASK)])
        assert resolver.resolve("EMAIL", "llm_output") == DLPAction.MASK
        assert resolver.resolve("SSN", "llm_output") == DLPAction.MASK
        assert resolver.resolve("CREDIT_CARD", "llm_output") == DLPAction.MASK

    def test_wildcard_direction_matches_any_direction(self) -> None:
        resolver = DLPPolicyResolver([rule(direction="*", action=DLPAction.BLOCK)])
        assert resolver.resolve("EMAIL", "llm_input") == DLPAction.BLOCK
        assert resolver.resolve("EMAIL", "tool_output") == DLPAction.BLOCK
        assert resolver.resolve("EMAIL", "mcp_response") == DLPAction.BLOCK

    def test_exact_match_outranks_wildcard_at_same_scope(self) -> None:
        resolver = DLPPolicyResolver(
            [
                rule(entity_type="EMAIL", action=DLPAction.BLOCK),
                rule(entity_type="*", action=DLPAction.AUDIT),
            ]
        )
        assert resolver.resolve("EMAIL", "llm_output") == DLPAction.BLOCK
        assert resolver.resolve("SSN", "llm_output") == DLPAction.AUDIT

    def test_exact_direction_outranks_wildcard_direction(self) -> None:
        resolver = DLPPolicyResolver(
            [
                rule(direction="llm_input", action=DLPAction.BLOCK),
                rule(direction="*", action=DLPAction.AUDIT),
            ]
        )
        assert resolver.resolve("EMAIL", "llm_input") == DLPAction.BLOCK
        assert resolver.resolve("EMAIL", "llm_output") == DLPAction.AUDIT

    def test_no_match_does_not_fall_through_to_wildcard(self) -> None:
        resolver = DLPPolicyResolver([rule(entity_type="SSN", action=DLPAction.BLOCK)])
        assert resolver.resolve("EMAIL", "llm_output") == DLPAction.ALLOW


class TestPolicyResolverScopeMatching:
    def test_agent_rule_does_not_apply_to_other_agent(self) -> None:
        resolver = DLPPolicyResolver([rule(scope=PolicyScope.AGENT, scope_id="a1", action=DLPAction.BLOCK)])
        assert resolver.resolve("EMAIL", "llm_output", agent_id="a2") == DLPAction.ALLOW

    def test_workspace_rule_applies_to_any_agent_in_workspace(self) -> None:
        resolver = DLPPolicyResolver(
            [
                rule(
                    scope=PolicyScope.WORKSPACE,
                    scope_id="w1",
                    action=DLPAction.BLOCK,
                )
            ]
        )
        assert resolver.resolve("EMAIL", "llm_output", agent_id="a1", workspace_id="w1") == DLPAction.BLOCK
        assert resolver.resolve("EMAIL", "llm_output", agent_id="a2", workspace_id="w1") == DLPAction.BLOCK

    def test_org_rule_applies_to_any_workspace(self) -> None:
        resolver = DLPPolicyResolver([rule(scope=PolicyScope.ORG, scope_id="o1", action=DLPAction.BLOCK)])
        assert resolver.resolve("EMAIL", "llm_output", workspace_id="w1", org_id="o1") == DLPAction.BLOCK
        assert resolver.resolve("EMAIL", "llm_output", workspace_id="w2", org_id="o1") == DLPAction.BLOCK

    def test_agent_rule_does_not_apply_when_no_agent_id(self) -> None:
        resolver = DLPPolicyResolver([rule(scope=PolicyScope.AGENT, scope_id="a1", action=DLPAction.BLOCK)])
        assert resolver.resolve("EMAIL", "llm_output") == DLPAction.ALLOW

    def test_workspace_rule_does_not_apply_when_no_workspace_id(self) -> None:
        resolver = DLPPolicyResolver(
            [
                rule(
                    scope=PolicyScope.WORKSPACE,
                    scope_id="w1",
                    action=DLPAction.BLOCK,
                )
            ]
        )
        assert resolver.resolve("EMAIL", "llm_output", org_id="o1") == DLPAction.ALLOW

    def test_org_rule_does_not_apply_when_no_org_id(self) -> None:
        resolver = DLPPolicyResolver([rule(scope=PolicyScope.ORG, scope_id="o1", action=DLPAction.BLOCK)])
        assert resolver.resolve("EMAIL", "llm_output") == DLPAction.ALLOW


class TestPolicyResolverRules:
    def test_rules_property_returns_copy(self) -> None:
        rules = [rule()]
        resolver = DLPPolicyResolver(rules)
        returned = resolver.rules
        returned.clear()
        assert len(resolver.rules) == 1


class TestPolicyResolverCombined:
    def test_full_scenario_org_locked_workspace_relaxes_agent_blocks(self) -> None:
        """Realistic scenario from design.md §D4.

        Org: EMAIL→BLOCK locked (security team red line).
        Workspace: EMAIL→AUDIT (allow for testing).
        Agent: EMAIL→ALLOW (workaround for fake data).

        For agent-level requests, org's locked BLOCK must win.
        """
        resolver = DLPPolicyResolver(
            [
                rule(
                    scope=PolicyScope.AGENT,
                    scope_id="a1",
                    action=DLPAction.ALLOW,
                ),
                rule(
                    scope=PolicyScope.WORKSPACE,
                    scope_id="w1",
                    action=DLPAction.AUDIT,
                ),
                rule(
                    scope=PolicyScope.ORG,
                    scope_id="o1",
                    action=DLPAction.BLOCK,
                    is_locked=True,
                ),
            ]
        )
        assert resolver.resolve("EMAIL", "llm_output", agent_id="a1", workspace_id="w1", org_id="o1") == DLPAction.BLOCK

    def test_wildcard_catchall_at_default_for_unmatched_entity(self) -> None:
        resolver = DLPPolicyResolver(
            [
                rule(
                    entity_type="SSN",
                    scope=PolicyScope.ORG,
                    scope_id="o1",
                    action=DLPAction.BLOCK,
                ),
                rule(entity_type="*", scope=PolicyScope.DEFAULT, action=DLPAction.MASK),
            ]
        )
        assert resolver.resolve("EMAIL", "llm_output", org_id="o1") == DLPAction.MASK
        assert resolver.resolve("SSN", "llm_output", org_id="o1") == DLPAction.BLOCK


class TestPolicyResolverMaskFormat:
    def test_mask_format_is_preserved(self) -> None:
        resolver = DLPPolicyResolver(
            [
                rule(
                    entity_type="EMAIL",
                    action=DLPAction.MASK,
                    mask_format="[REDACTED_EMAIL]",
                )
            ]
        )
        matched = next(r for r in resolver.rules if r.entity_type == "EMAIL")
        assert matched.mask_format == "[REDACTED_EMAIL]"
