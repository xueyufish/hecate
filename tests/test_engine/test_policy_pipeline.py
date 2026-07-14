"""Tests for the composable tool policy pipeline and layers."""

from __future__ import annotations

from hecate.engine.policy_layers import (
    ModeLayer,
    PluginAvailabilityLayer,
    ProfileLayer,
    SecurityLayer,
    VisibilityLayer,
)
from hecate.engine.policy_pipeline import (
    PermissionMode,
    PolicyContext,
    PolicyDecision,
    ToolInfo,
    ToolPolicyPipeline,
)
from hecate.engine.tool_access import ToolAccessPolicy


def _make_tool(
    name: str = "web_search",
    source: str = "builtin",
    available_when: str | None = None,
    risk_level: str = "low",
    sandbox_enabled: bool = False,
    approval_required: bool = False,
    plugin_name: str | None = None,
    mcp_server: str | None = None,
) -> ToolInfo:
    return ToolInfo(
        name=name,
        source=source,
        available_when=available_when,
        risk_level=risk_level,
        sandbox_enabled=sandbox_enabled,
        approval_required=approval_required,
        plugin_name=plugin_name,
        mcp_server=mcp_server,
    )


def _make_ctx(
    arguments: dict | None = None,
    workspace_root: str | None = None,
    rules: list | None = None,
) -> PolicyContext:
    return PolicyContext(
        arguments=arguments or {},
        workspace_root=workspace_root,
        rules=rules or [],
    )


# ---------------------------------------------------------------------------
# Pipeline tests
# ---------------------------------------------------------------------------


def test_pipeline_deny_short_circuits() -> None:
    """DENY from any layer stops evaluation."""
    calls: list[str] = []

    class _TrackLayer:
        def __init__(self, name: str, decision: PolicyDecision) -> None:
            self._name = name
            self._decision = decision

        @property
        def name(self) -> str:
            return self._name

        def evaluate(self, tool: ToolInfo, ctx: PolicyContext) -> PolicyDecision:
            calls.append(self._name)
            return self._decision

    pipeline = ToolPolicyPipeline(
        [
            _TrackLayer("L0", PolicyDecision.DENY),
            _TrackLayer("L1", PolicyDecision.ALLOW),
        ]
    )
    decision, results = pipeline.evaluate_execution(_make_tool(), _make_ctx())
    assert decision == PolicyDecision.DENY
    assert calls == ["L0"]  # L1 never evaluated


def test_pipeline_all_pass_returns_allow() -> None:
    """All PASSTHROUGH returns ALLOW."""
    layer = ProfileLayer(rules=[])
    pipeline = ToolPolicyPipeline([layer])
    decision, _ = pipeline.evaluate_execution(_make_tool(), _make_ctx())
    assert decision == PolicyDecision.ALLOW


def test_pipeline_visibility_filters_hidden() -> None:
    """HIDE decision removes tool from visibility list."""
    visibility = VisibilityLayer()
    pipeline = ToolPolicyPipeline([visibility])
    tools = [
        {"name": "visible_tool"},
        {"name": "hidden_tool", "available_when": "False"},
    ]
    ctx = _make_ctx()
    visible = pipeline.evaluate_visibility(tools, ctx)
    assert len(visible) == 1
    assert visible[0]["name"] == "visible_tool"


# ---------------------------------------------------------------------------
# PluginAvailabilityLayer tests
# ---------------------------------------------------------------------------


def test_plugin_layer_builtin_always_allowed() -> None:
    layer = PluginAvailabilityLayer()
    assert layer.evaluate(_make_tool(source="builtin"), _make_ctx()) == PolicyDecision.ALLOW


def test_plugin_layer_disabled_plugin_denied() -> None:
    layer = PluginAvailabilityLayer(
        plugin_status_fn=lambda name: False,
    )
    tool = _make_tool(source="custom", plugin_name="my_plugin")
    assert layer.evaluate(tool, _make_ctx()) == PolicyDecision.DENY


def test_plugin_layer_mcp_server_unregistered() -> None:
    layer = PluginAvailabilityLayer(
        mcp_server_fn=lambda name: False,
    )
    tool = _make_tool(source="mcp", mcp_server="unregistered_srv")
    assert layer.evaluate(tool, _make_ctx()) == PolicyDecision.DENY


def test_plugin_layer_enabled_plugin_allowed() -> None:
    layer = PluginAvailabilityLayer(
        plugin_status_fn=lambda name: True,
    )
    tool = _make_tool(source="custom", plugin_name="enabled_plugin")
    assert layer.evaluate(tool, _make_ctx()) == PolicyDecision.ALLOW


# ---------------------------------------------------------------------------
# ProfileLayer tests
# ---------------------------------------------------------------------------


def test_profile_no_rules_passthrough() -> None:
    layer = ProfileLayer(rules=[])
    assert layer.evaluate(_make_tool(), _make_ctx()) == PolicyDecision.PASSTHROUGH


def test_profile_deny_rule_matches() -> None:
    layer = ProfileLayer(
        rules=[
            {"action": "deny", "tool_pattern": "bash", "priority": 0},
        ]
    )
    tool = _make_tool(name="bash")
    assert layer.evaluate(tool, _make_ctx()) == PolicyDecision.DENY


def test_profile_allow_rule_matches() -> None:
    layer = ProfileLayer(
        rules=[
            {"action": "allow", "tool_pattern": "web_*", "priority": 0},
        ]
    )
    tool = _make_tool(name="web_search")
    assert layer.evaluate(tool, _make_ctx()) == PolicyDecision.ALLOW


def test_profile_glob_pattern_matching() -> None:
    layer = ProfileLayer(
        rules=[
            {"action": "deny", "tool_pattern": "terminal(*)", "priority": 0},
        ]
    )
    tool = _make_tool(name="terminal(git:status)")
    assert layer.evaluate(tool, _make_ctx()) == PolicyDecision.DENY


def test_profile_arg_conditions_must_match() -> None:
    layer = ProfileLayer(
        rules=[
            {
                "action": "deny",
                "tool_pattern": "write_file",
                "priority": 0,
                "arg_conditions": {"path": "/etc/*"},
            },
        ]
    )
    ctx = _make_ctx(arguments={"path": "/workspace/file.txt"})
    tool = _make_tool(name="write_file")
    assert layer.evaluate(tool, ctx) == PolicyDecision.PASSTHROUGH  # condition doesn't match

    ctx2 = _make_ctx(arguments={"path": "/etc/passwd"})
    assert layer.evaluate(tool, ctx2) == PolicyDecision.DENY


def test_profile_priority_ordering() -> None:
    layer = ProfileLayer(
        rules=[
            {"action": "allow", "tool_pattern": "*", "priority": 0},
            {"action": "deny", "tool_pattern": "bash", "priority": 10},
        ]
    )
    tool = _make_tool(name="bash")
    # deny with higher priority should win
    assert layer.evaluate(tool, _make_ctx()) == PolicyDecision.DENY


# ---------------------------------------------------------------------------
# VisibilityLayer tests
# ---------------------------------------------------------------------------


def test_visibility_no_expression_allowed() -> None:
    layer = VisibilityLayer()
    tool = _make_tool(available_when=None)
    assert layer.evaluate(tool, _make_ctx()) == PolicyDecision.ALLOW


def test_visibility_expression_passes() -> None:
    layer = VisibilityLayer()
    tool = _make_tool(available_when="user_role == 'admin'")
    ctx = PolicyContext(execution_context={"user_role": "admin"})
    assert layer.evaluate(tool, ctx) == PolicyDecision.ALLOW


def test_visibility_expression_fails_returns_hide() -> None:
    layer = VisibilityLayer()
    tool = _make_tool(available_when="user_role == 'admin'")
    ctx = PolicyContext(execution_context={"user_role": "guest"})
    assert layer.evaluate(tool, ctx) == PolicyDecision.HIDE


def test_visibility_expression_error_fail_closed() -> None:
    layer = VisibilityLayer()
    tool = _make_tool(available_when="undefined_var == True")
    ctx = _make_ctx()
    assert layer.evaluate(tool, ctx) == PolicyDecision.HIDE


# ---------------------------------------------------------------------------
# SecurityLayer tests
# ---------------------------------------------------------------------------


def test_security_layer_low_risk_execute() -> None:
    layer = SecurityLayer(policy=ToolAccessPolicy())
    tool = _make_tool(risk_level="low", sandbox_enabled=False)
    assert layer.evaluate(tool, _make_ctx()) == PolicyDecision.ALLOW


def test_security_layer_sandbox_enabled() -> None:
    layer = SecurityLayer(policy=ToolAccessPolicy())
    tool = _make_tool(risk_level="low", sandbox_enabled=True)
    assert layer.evaluate(tool, _make_ctx()) == PolicyDecision.EXECUTE_SANDBOX


def test_security_layer_critical_require_approval() -> None:
    layer = SecurityLayer(policy=ToolAccessPolicy())
    tool = _make_tool(risk_level="critical", sandbox_enabled=False)
    assert layer.evaluate(tool, _make_ctx()) == PolicyDecision.REQUIRE_APPROVAL


# ---------------------------------------------------------------------------
# ModeLayer tests
# ---------------------------------------------------------------------------


def test_mode_default_passthrough() -> None:
    layer = ModeLayer(mode=PermissionMode.DEFAULT)
    assert layer.evaluate(_make_tool(), _make_ctx()) == PolicyDecision.PASSTHROUGH


def test_mode_restricted_denies_non_allowlisted() -> None:
    layer = ModeLayer(mode=PermissionMode.RESTRICTED, allowlist=["web_search"])
    tool_in_list = _make_tool(name="web_search")
    tool_not_in_list = _make_tool(name="bash")
    assert layer.evaluate(tool_in_list, _make_ctx()) == PolicyDecision.PASSTHROUGH
    assert layer.evaluate(tool_not_in_list, _make_ctx()) == PolicyDecision.DENY


def test_mode_audit_overrides_deny() -> None:
    layer = ModeLayer(mode=PermissionMode.AUDIT)
    assert layer.override_decision(PolicyDecision.DENY, "test_tool") == PolicyDecision.ALLOW


def test_mode_audit_preserves_require_approval() -> None:
    layer = ModeLayer(mode=PermissionMode.AUDIT)
    assert layer.override_decision(PolicyDecision.REQUIRE_APPROVAL, "test_tool") == PolicyDecision.REQUIRE_APPROVAL


def test_mode_non_audit_no_override() -> None:
    layer = ModeLayer(mode=PermissionMode.DEFAULT)
    assert layer.override_decision(PolicyDecision.DENY, "test_tool") == PolicyDecision.DENY


# ---------------------------------------------------------------------------
# Full pipeline integration tests
# ---------------------------------------------------------------------------


def test_full_pipeline_default_mode_allows_safe_tool() -> None:
    pipeline = ToolPolicyPipeline(
        [
            PluginAvailabilityLayer(),
            ProfileLayer(rules=[]),
            VisibilityLayer(),
            SecurityLayer(),
            ModeLayer(mode=PermissionMode.DEFAULT),
        ]
    )
    tool = _make_tool(name="web_search", risk_level="low")
    decision, _ = pipeline.evaluate_execution(tool, _make_ctx())
    assert decision == PolicyDecision.ALLOW


def test_full_pipeline_restricted_mode_denies_unlisted() -> None:
    pipeline = ToolPolicyPipeline(
        [
            PluginAvailabilityLayer(),
            ProfileLayer(rules=[]),
            VisibilityLayer(),
            SecurityLayer(),
            ModeLayer(mode=PermissionMode.RESTRICTED, allowlist=["web_search"]),
        ]
    )
    tool = _make_tool(name="dangerous_tool", risk_level="low")
    decision, _ = pipeline.evaluate_execution(tool, _make_ctx())
    assert decision == PolicyDecision.DENY
