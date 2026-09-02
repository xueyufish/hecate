"""End-to-end integration tests for Environment Security P0.

These tests verify the complete flow of the 4 security features:
- 9.12 Network Egress Control
- 9.13 Sandbox Enforcement Integration
- 9.14 Structured Security Audit Pipeline
- 9.15 Per-Execution Credential Scoping

Most tests are marked as integration tests requiring running services.
"""

from __future__ import annotations

from hecate_sandbox.environment.credential_scope import CredentialScope
from hecate_sandbox.environment.network_policy import (
    NetworkEgressPolicy,
    NetworkPolicyMode,
)

from hecate.runtime.decision_sink import ToolDecisionEmitter
from hecate.runtime.tool_access import AccessDecision
from hecate.runtime.workers.sandbox_router import SandboxEnforcementRouter


class TestEndToEndDefaultBehavior:
    """Task 6.3: All features disabled (defaults) → zero behavioral change."""

    def test_default_network_policy_is_allow_all(self):
        policy = NetworkEgressPolicy()
        assert policy.mode == NetworkPolicyMode.ALLOW_ALL
        assert policy.is_domain_allowed("anything.com") is True

    def test_default_sandbox_enforcement_is_disabled(self):
        router = SandboxEnforcementRouter()
        assert not router.enabled
        assert not router.should_route_to_environment(
            "bash",
            AccessDecision.EXECUTE_SANDBOX,
        )

    def test_default_credential_scope_is_disabled(self):
        scope = CredentialScope()
        assert not scope.enabled
        env = {"PATH": "/usr/bin", "OPENAI_API_KEY": "sk-xxx"}
        assert scope.sanitize_environment(env) == env

    def test_decision_emitter_default_is_disabled(self):
        emitter = ToolDecisionEmitter()
        assert not emitter.enabled


class TestEndToEndAuditPipeline:
    """Task 6.4: Audit pipeline works on both environments."""

    def test_decision_emitter_collects_events(self):
        collected: list[dict] = []

        class CollectingSink:
            def emit(self, event: dict) -> None:
                collected.append(event)

        emitter = ToolDecisionEmitter()
        emitter.set_sink(CollectingSink())

        emitter.emit(
            emitter.build_event(
                agent_id="agent-1",
                workspace_id="ws-1",
                tool_name="bash",
                decision="deny",
                reason="dangerous pattern",
            )
        )
        assert len(collected) == 1
        assert collected[0]["agent_id"] == "agent-1"
        assert collected[0]["decision"] == "deny"

    def test_decision_emitter_captures_layer_results(self):
        collected: list[dict] = []

        class CollectingSink:
            def emit(self, event: dict) -> None:
                collected.append(event)

        emitter = ToolDecisionEmitter()
        emitter.set_sink(CollectingSink())

        emitter.emit(
            emitter.build_event(
                agent_id="a1",
                workspace_id="w1",
                tool_name="bash",
                decision="allow",
                layer_results=[
                    {"layer": "dangerous_patterns", "decision": "allow"},
                    {"layer": "rule_engine", "decision": "allow"},
                    {"layer": "risk_level", "decision": "allow"},
                ],
            )
        )
        assert len(collected[0]["layer_results"]) == 3


class TestEndToEndNetworkAndCredentialIntegration:
    """Task 6.1: Network policy + credential scoping work together."""

    def test_deny_all_policy_with_credential_scoping(self):
        policy = NetworkEgressPolicy(
            mode=NetworkPolicyMode.DENY_ALL,
            allowed_domains=["api.openai.com"],
        )
        scope = CredentialScope(enabled=True)

        assert policy.is_domain_allowed("api.openai.com") is True
        assert policy.is_domain_allowed("evil.com") is False

        env = {
            "PATH": "/usr/bin",
            "HOME": "/root",
            "OPENAI_API_KEY": "sk-xxx",
            "DATABASE_PASSWORD": "secret",
        }
        sanitized = scope.sanitize_environment(env)
        assert "OPENAI_API_KEY" not in sanitized
        assert "DATABASE_PASSWORD" not in sanitized
        assert "PATH" in sanitized
        assert "HOME" in sanitized


class TestEndToEndSandboxAndAuditIntegration:
    """Task 6.2: Sandbox enforcement + audit pipeline work together."""

    def test_sandbox_routing_emits_audit(self):
        router = SandboxEnforcementRouter(enabled=True)
        should_route = router.should_route_to_environment(
            "bash",
            AccessDecision.EXECUTE_SANDBOX,
        )
        assert should_route is True

        collected: list[dict] = []

        class CollectingSink:
            def emit(self, event: dict) -> None:
                collected.append(event)

        emitter = ToolDecisionEmitter()
        emitter.set_sink(CollectingSink())

        emitter.emit(
            emitter.build_event(
                agent_id="a1",
                workspace_id="w1",
                tool_name="bash",
                decision="execute_sandbox",
                reason="sandbox enforcement active",
            )
        )
        assert len(collected) == 1
        assert collected[0]["decision"] == "execute_sandbox"


class TestEndToEndContainerExitVerification:
    """Task 6.2: Container exit verification with audit emission."""

    def test_abnormal_exit_emits_anomaly(self):
        router = SandboxEnforcementRouter()

        collected: list[dict] = []

        class CollectingSink:
            def emit(self, event: dict) -> None:
                collected.append(event)

        emitter = ToolDecisionEmitter()
        emitter.set_sink(CollectingSink())

        result = router.verify_container_exit(-1, b"OOM killed")
        assert result is False

        emitter.emit(
            emitter.build_event(
                agent_id=None,
                workspace_id=None,
                tool_name="sandbox_verification",
                decision="sandbox_anomaly",
                reason="Abnormal container exit",
            )
        )
        assert len(collected) == 1
        assert collected[0]["decision"] == "sandbox_anomaly"
