from __future__ import annotations

import pytest

from hecate.engine.tool_access import (
    AccessDecision,
    ApprovalCallback,
    ApprovalDecision,
    ApprovalScope,
    RiskLevel,
    RuleAction,
    ToolAccessPolicy,
    ToolRule,
)


class TestRiskLevel:
    def test_four_members(self) -> None:
        assert len(list(RiskLevel)) == 4

    def test_string_values(self) -> None:
        assert RiskLevel.LOW == "low"
        assert RiskLevel.MEDIUM == "medium"
        assert RiskLevel.HIGH == "high"
        assert RiskLevel.CRITICAL == "critical"

    def test_strenum_comparison(self) -> None:
        assert RiskLevel.CRITICAL == "critical"


class TestAccessDecision:
    def test_four_members(self) -> None:
        assert len(list(AccessDecision)) == 4

    def test_string_values(self) -> None:
        assert AccessDecision.EXECUTE == "execute"
        assert AccessDecision.EXECUTE_SANDBOX == "execute_sandbox"
        assert AccessDecision.REQUIRE_APPROVAL == "require_approval"
        assert AccessDecision.DENY == "deny"


class TestApprovalScope:
    def test_four_members(self) -> None:
        assert len(list(ApprovalScope)) == 4

    def test_default_scope(self) -> None:
        decision = ApprovalDecision(approved=True)
        assert decision.scope == ApprovalScope.ONCE


class TestRuleAction:
    def test_three_members(self) -> None:
        assert len(list(RuleAction)) == 3

    def test_string_values(self) -> None:
        assert RuleAction.ALLOW == "allow"
        assert RuleAction.DENY == "deny"
        assert RuleAction.ASK == "ask"


class TestApprovalDecision:
    def test_approved_defaults(self) -> None:
        d = ApprovalDecision(approved=True)
        assert d.approved is True
        assert d.reason == ""
        assert d.scope == ApprovalScope.ONCE

    def test_denied_with_reason(self) -> None:
        d = ApprovalDecision(approved=False, reason="Timeout")
        assert d.approved is False
        assert d.reason == "Timeout"


class TestToolRule:
    def test_construction(self) -> None:
        r = ToolRule(action=RuleAction.DENY, pattern="terminal(rm *)")
        assert r.action == RuleAction.DENY
        assert r.pattern == "terminal(rm *)"

    def test_default_priority(self) -> None:
        r = ToolRule(action=RuleAction.ALLOW, pattern="*")
        assert r.priority == 0


class TestApprovalCallback:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            ApprovalCallback()

    def test_subclass_works(self) -> None:
        class MyCallback(ApprovalCallback):
            async def request_approval(self, tool_name, arguments, risk_level, context):
                return ApprovalDecision(approved=True)

        cb = MyCallback()
        assert cb is not None


class TestToolAccessPolicyInstantiation:
    def test_direct_instantiation(self) -> None:
        policy = ToolAccessPolicy()
        assert policy is not None


class TestToolAccessPolicyEvaluate:
    def setup_method(self) -> None:
        self.policy = ToolAccessPolicy()

    def test_no_rules_low_risk_execute(self) -> None:
        result = self.policy.evaluate(
            {"risk_level": "low", "approval_required": False, "sandbox_enabled": False},
            rules=[],
            context={},
        )
        assert result == AccessDecision.EXECUTE

    def test_no_rules_medium_sandbox_enabled(self) -> None:
        result = self.policy.evaluate(
            {"risk_level": "medium", "approval_required": False, "sandbox_enabled": True},
            rules=[],
            context={},
        )
        assert result == AccessDecision.EXECUTE_SANDBOX

    def test_no_rules_high_no_sandbox(self) -> None:
        result = self.policy.evaluate(
            {"risk_level": "high", "approval_required": False, "sandbox_enabled": False},
            rules=[],
            context={},
        )
        assert result == AccessDecision.REQUIRE_APPROVAL

    def test_no_rules_critical_sandbox_still_approval(self) -> None:
        result = self.policy.evaluate(
            {"risk_level": "critical", "approval_required": False, "sandbox_enabled": True},
            rules=[],
            context={},
        )
        assert result == AccessDecision.REQUIRE_APPROVAL

    def test_approval_required_overrides_low(self) -> None:
        result = self.policy.evaluate(
            {"risk_level": "low", "approval_required": True, "sandbox_enabled": False},
            rules=[],
            context={},
        )
        assert result == AccessDecision.REQUIRE_APPROVAL

    def test_deny_rule_overrides_everything(self) -> None:
        result = self.policy.evaluate(
            {"risk_level": "low", "approval_required": False, "sandbox_enabled": False},
            rules=[ToolRule(action=RuleAction.DENY, pattern="*")],
            context={"tool_name": "any_tool"},
        )
        assert result == AccessDecision.DENY

    def test_allow_rule_overrides_high_risk(self) -> None:
        result = self.policy.evaluate(
            {"risk_level": "high", "approval_required": False, "sandbox_enabled": False},
            rules=[ToolRule(action=RuleAction.ALLOW, pattern="*")],
            context={"tool_name": "any_tool"},
        )
        assert result == AccessDecision.EXECUTE

    def test_ask_rule_overrides_low_risk(self) -> None:
        result = self.policy.evaluate(
            {"risk_level": "low", "approval_required": False, "sandbox_enabled": False},
            rules=[ToolRule(action=RuleAction.ASK, pattern="*")],
            context={"tool_name": "any_tool"},
        )
        assert result == AccessDecision.REQUIRE_APPROVAL


class TestRuleEvaluationOrder:
    def setup_method(self) -> None:
        self.policy = ToolAccessPolicy()

    def test_deny_precedence_over_allow(self) -> None:
        result = self.policy.evaluate(
            {"risk_level": "low", "approval_required": False, "sandbox_enabled": False},
            rules=[
                ToolRule(action=RuleAction.DENY, pattern="terminal(*)"),
                ToolRule(action=RuleAction.ALLOW, pattern="terminal(git:*)"),
            ],
            context={"tool_name": "terminal(git:push)"},
        )
        assert result == AccessDecision.DENY

    def test_ask_precedence_over_allow(self) -> None:
        result = self.policy.evaluate(
            {"risk_level": "low", "approval_required": False, "sandbox_enabled": False},
            rules=[
                ToolRule(action=RuleAction.ASK, pattern="write_file(.env*)"),
                ToolRule(action=RuleAction.ALLOW, pattern="write_file(*)"),
            ],
            context={"tool_name": "write_file(.env.production)"},
        )
        assert result == AccessDecision.REQUIRE_APPROVAL

    def test_no_rule_match_fallback_to_risk(self) -> None:
        result = self.policy.evaluate(
            {"risk_level": "high", "approval_required": False, "sandbox_enabled": False},
            rules=[ToolRule(action=RuleAction.ALLOW, pattern="terminal(git:*)")],
            context={"tool_name": "execute_python"},
        )
        assert result == AccessDecision.REQUIRE_APPROVAL


class TestPatternMatching:
    def setup_method(self) -> None:
        self.policy = ToolAccessPolicy()

    def test_exact_match(self) -> None:
        result = self.policy.evaluate(
            {"risk_level": "high", "approval_required": False, "sandbox_enabled": False},
            rules=[ToolRule(action=RuleAction.ALLOW, pattern="terminal")],
            context={"tool_name": "terminal"},
        )
        assert result == AccessDecision.EXECUTE

    def test_wildcard_match(self) -> None:
        result = self.policy.evaluate(
            {"risk_level": "high", "approval_required": False, "sandbox_enabled": False},
            rules=[ToolRule(action=RuleAction.ALLOW, pattern="terminal(git:*)")],
            context={"tool_name": "terminal(git:push)"},
        )
        assert result == AccessDecision.EXECUTE

    def test_catch_all_match(self) -> None:
        result = self.policy.evaluate(
            {"risk_level": "high", "approval_required": False, "sandbox_enabled": False},
            rules=[ToolRule(action=RuleAction.DENY, pattern="*")],
            context={"tool_name": "anything"},
        )
        assert result == AccessDecision.DENY

    def test_no_match(self) -> None:
        result = self.policy.evaluate(
            {"risk_level": "low", "approval_required": False, "sandbox_enabled": False},
            rules=[ToolRule(action=RuleAction.DENY, pattern="terminal(rm:*)")],
            context={"tool_name": "terminal(git:push)"},
        )
        assert result == AccessDecision.EXECUTE
