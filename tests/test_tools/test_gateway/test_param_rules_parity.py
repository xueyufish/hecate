"""Parameter-level DENY parity across entry paths (task 2.1).

A parameter-conditional rule (forbid writing ``.env``, forbid dangerous shell
flags) must produce the same denial whether the evaluation runs directly on
the pipeline (REST/chat path shape) or through the gateway boundary (MCP
path) — parameter content, not the digest, drives the rule.
"""

from __future__ import annotations

from typing import Any

from hecate.tools.gateway.authz import CallerIdentity, GatewayAuthorizer
from hecate.tools.policy import (
    PolicyContext,
    PolicyDecision,
    ToolInfo,
    ToolPolicyPipeline,
)
from hecate.tools.policy.policy_layers import (
    PluginAvailabilityLayer,
    ProfileLayer,
    VisibilityLayer,
)

_ENV_WRITE_RULE: dict[str, Any] = {
    "action": "deny",
    "tool_pattern": "write_file",
    "priority": 10,
    "arg_conditions": {"path": "*.env"},
}

_DANGEROUS_SHELL_RULE: dict[str, Any] = {
    "action": "deny",
    "tool_pattern": "run_shell",
    "priority": 10,
    "arg_conditions": {"command": "*rm -rf*"},
}


def _pipeline() -> ToolPolicyPipeline:
    return ToolPolicyPipeline(
        layers=[
            PluginAvailabilityLayer(),
            ProfileLayer(rules=[_ENV_WRITE_RULE, _DANGEROUS_SHELL_RULE]),
            VisibilityLayer(),
        ]
    )


def _identity() -> CallerIdentity:
    return CallerIdentity(
        workspace_id="ws-1",
        scope="workspace",
        authenticated=True,
        actor="apikey:1",
    )


def _tool(name: str) -> dict[str, Any]:
    return {"name": name, "source": "builtin", "risk_level": "medium"}


class TestParameterDenyParity:
    def test_env_write_denied_on_both_paths(self) -> None:
        arguments = {"path": "project/.env", "content": "SECRET=1"}
        direct = _pipeline().evaluate_execution(
            ToolInfo(name="write_file", tool_def=_tool("write_file")),
            PolicyContext(workspace_id="ws-1", arguments=arguments),
        )
        gateway = GatewayAuthorizer(pipeline=_pipeline()).authorize(
            _tool("write_file"), _identity(), arguments=arguments
        )
        assert direct[0] is PolicyDecision.DENY
        assert gateway == (False, "denied by policy (deny)")

    def test_dangerous_shell_denied_on_both_paths(self) -> None:
        arguments = {"command": "rm -rf / --no-preserve-root"}
        direct = _pipeline().evaluate_execution(
            ToolInfo(name="run_shell", tool_def=_tool("run_shell")),
            PolicyContext(workspace_id="ws-1", arguments=arguments),
        )
        gateway = GatewayAuthorizer(pipeline=_pipeline()).authorize(
            _tool("run_shell"), _identity(), arguments=arguments
        )
        assert direct[0] is PolicyDecision.DENY
        assert gateway == (False, "denied by policy (deny)")

    def test_benign_arguments_pass_on_both_paths(self) -> None:
        arguments = {"path": "project/settings.yaml", "content": "a: 1"}
        direct = _pipeline().evaluate_execution(
            ToolInfo(name="write_file", tool_def=_tool("write_file")),
            PolicyContext(workspace_id="ws-1", arguments=arguments),
        )
        gateway = GatewayAuthorizer(pipeline=_pipeline()).authorize(
            _tool("write_file"), _identity(), arguments=arguments
        )
        assert direct[0] is PolicyDecision.ALLOW
        assert gateway[0] is True

    def test_without_arguments_gateway_stays_open_for_unconditional_rules(
        self,
    ) -> None:
        # No arguments supplied (legacy caller): conditional rules cannot
        # match, so the call is not denied by them — content-bearing callers
        # must pass arguments for the rules to apply.
        gateway = GatewayAuthorizer(pipeline=_pipeline()).authorize(_tool("write_file"), _identity())
        assert gateway[0] is True


class TestEnvelopeFlowsIntoContext:
    def test_gateway_context_carries_verified_envelope(self) -> None:
        identity = CallerIdentity(
            workspace_id="ws-1",
            scope="workspace",
            authenticated=True,
            actor="apikey:7",
            agent_principal="agent-3",
            on_behalf_of="user-2",
            delegation_id="dele-1",
            data_domains=("inventory",),
            deployment_id="dealer-app",
        )
        context = GatewayAuthorizer._context(identity, arguments={"a": 1})
        assert context.actor == "apikey:7"
        assert context.agent_principal == "agent-3"
        assert context.on_behalf_of == "user-2"
        assert context.delegation_id == "dele-1"
        assert context.data_domains == ("inventory",)
        assert context.deployment_id == "dealer-app"
        assert context.arguments == {"a": 1}

    def test_self_asserted_identity_never_enters_context(self) -> None:
        # The envelope comes from the verified CallerIdentity; a caller cannot
        # inject actor/agent_principal through tool arguments.
        identity = CallerIdentity(
            workspace_id="ws-1",
            scope="workspace",
            authenticated=True,
            actor="apikey:7",
        )
        context = GatewayAuthorizer._context(
            identity,
            arguments={
                "path": "x",
                "actor": "admin",
                "agent_principal": "agent-9",
                "data_domains": ["finance"],
            },
        )
        assert context.actor == "apikey:7"
        assert context.agent_principal is None
        assert context.data_domains == ()
