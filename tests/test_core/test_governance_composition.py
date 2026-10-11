"""Composition-level governance wiring tests (task 2.2).

Covers: REST/MCP decision parity, per-call judgment across tasks (no result
caching), refusal of context-free executors, and the rollback switch not
disabling the safety floor.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from hecate_governance.action import ActionDecision
from hecate_governance.policy import (
    GovernanceService,
    PolicyContext,
    PolicyOutcome,
    PolicyProfile,
)
from hecate_governance.principal import ActionPrincipal, PrincipalSource

from hecate.core.composition.governance import (
    GovernanceDeniedError,
    GovernanceMode,
    PlatformGovernance,
    PrincipalLifecycleRevocation,
    build_platform_governance,
)
from hecate.core.composition.runtime_port_adapter import _ProductionRuntimePort
from hecate.tools.gateway.authz import CallerIdentity, GatewayAuthorizer
from hecate.tools.policy import ToolPolicyPipeline
from hecate.tools.policy.policy_layers import ProfileLayer

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)

ENV_RULE: dict[str, Any] = {
    "action": "deny",
    "tool_pattern": "write_file",
    "priority": 10,
    "arg_conditions": {"path": "*.env"},
}


def _principal(actor: str = "agent:7") -> ActionPrincipal:
    return ActionPrincipal.from_verified_claims(
        {"sub": actor},
        source=PrincipalSource.PLATFORM_ENTRY,
        domains=("inventory",),
    )


class _RegistryStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, name: str, args: dict, context: dict | None = None) -> str:
        self.calls.append((name, args))
        return "ok"


class TestRestMcpParity:
    async def test_env_write_denied_on_both_entries(self) -> None:
        arguments = {"path": "project/.env", "content": "SECRET=1"}
        # MCP entry: gateway boundary evaluation.
        gateway = GatewayAuthorizer(pipeline=ToolPolicyPipeline(layers=[ProfileLayer(rules=[ENV_RULE])]))
        mcp_allowed, mcp_reason = gateway.authorize({"name": "write_file"}, _mcp_identity(), arguments=arguments)
        # REST/builtin entry: governance service over the same rules.
        governance = build_platform_governance(
            None, mode=GovernanceMode.ENFORCED, rules=[ENV_RULE], identity={"actor": "agent:7"}
        )
        decision = await governance.authorize_tool_call(name="write_file", arguments=arguments)
        assert mcp_allowed is False
        assert not decision.allowed
        assert decision.violation.value == "policy_hard_deny"
        assert "policy" in mcp_reason

    async def test_benign_call_allowed_on_both_entries(self) -> None:
        arguments = {"path": "project/settings.yaml"}
        gateway = GatewayAuthorizer(pipeline=ToolPolicyPipeline(layers=[ProfileLayer(rules=[ENV_RULE])]))
        mcp_allowed, _ = gateway.authorize({"name": "write_file"}, _mcp_identity(), arguments=arguments)
        governance = build_platform_governance(
            None, mode=GovernanceMode.ENFORCED, rules=[ENV_RULE], identity={"actor": "agent:7"}
        )
        decision = await governance.authorize_tool_call(name="write_file", arguments=arguments)
        assert mcp_allowed is True
        assert decision.allowed


def _mcp_identity() -> CallerIdentity:
    return CallerIdentity(
        workspace_id="ws-1",
        scope="workspace",
        authenticated=True,
        actor="apikey:1",
    )


class TaskScopedProfile(PolicyProfile):
    """Grants differ per task_ref — same principal/tool/arguments otherwise."""

    def __init__(self, allowed_tasks: set[str]) -> None:
        self._allowed = allowed_tasks

    @property
    def version(self) -> str:
        return "task-scoped-1"

    async def evaluate(self, context: PolicyContext) -> PolicyOutcome:
        if context.task_ref in self._allowed:
            return PolicyOutcome(ActionDecision.ALLOW, self.version)
        return PolicyOutcome(ActionDecision.DENY, self.version, "task not granted")


class TestPerCallJudgment:
    async def test_same_call_different_tasks_diverge_without_interference(self) -> None:
        service = GovernanceService(TaskScopedProfile(allowed_tasks={"task-a"}))
        governance = PlatformGovernance(service, GovernanceMode.ENFORCED)
        call = dict(context={"session_id": None}, identity=None)
        allowed = await governance.authorize_tool_call(
            name="inventory.write",
            arguments={"sku": "42"},
            context={"session_id": "task-a", "agent_id": "7"},
        )
        denied = await governance.authorize_tool_call(
            name="inventory.write",
            arguments={"sku": "42"},
            context={"session_id": "task-b", "agent_id": "7"},
        )
        del call
        assert allowed.allowed
        assert not denied.allowed
        # Repeating the allowed task still passes: no state leaked from the
        # denied evaluation (per-call judgment, no result cache).
        again = await governance.authorize_tool_call(
            name="inventory.write",
            arguments={"sku": "42"},
            context={"session_id": "task-a", "agent_id": "7"},
        )
        assert again.allowed


class _RevokedView(PrincipalLifecycleRevocation):
    def __init__(self) -> None:
        super().__init__(None, None)

    async def is_revoked(self, principal: ActionPrincipal) -> bool:
        return principal.actor == "agent:revoked"


class TestAssemblyRefusals:
    async def test_port_without_governance_refuses_execution(self) -> None:
        port = _ProductionRuntimePort(db=None, llm_service=None, tool_registry=_RegistryStub(), governance=None)
        with pytest.raises(RuntimeError, match="governance service not configured"):
            await port.tool_execute("write_file", {"path": "x"})

    async def test_denied_call_raises_and_registry_not_called(self) -> None:
        service = GovernanceService(
            TaskScopedProfile(allowed_tasks=set()),
            revocation=_RevokedView(),
        )
        registry = _RegistryStub()
        port = _ProductionRuntimePort(
            db=None,
            llm_service=None,
            tool_registry=registry,
            governance=PlatformGovernance(service, GovernanceMode.ENFORCED),
        )
        with pytest.raises(GovernanceDeniedError, match="task not granted"):
            await port.tool_execute("inventory.write", {"sku": "42"}, {"agent_id": "7"})
        assert registry.calls == []

    async def test_allowed_call_executes(self) -> None:
        service = GovernanceService(
            TaskScopedProfile(allowed_tasks={"task-a"}),
        )
        registry = _RegistryStub()
        port = _ProductionRuntimePort(
            db=None,
            llm_service=None,
            tool_registry=registry,
            governance=PlatformGovernance(service, GovernanceMode.ENFORCED),
        )
        result = await port.tool_execute("inventory.write", {"sku": "42"}, {"agent_id": "7", "session_id": "task-a"})
        assert result == "ok"
        assert len(registry.calls) == 1


class TestRollbackKeepsFloor:
    async def test_off_mode_still_denies_revoked_principal(self) -> None:
        # The rollback switch (mode=off) must not disable the safety floor:
        # a known-revoked principal is refused even with the policy allowing.
        service = GovernanceService(
            TaskScopedProfile(allowed_tasks={"task-a"}),
            revocation=_RevokedView(),
        )
        governance = PlatformGovernance(service, GovernanceMode.OFF)
        decision = await governance.authorize_tool_call(
            name="inventory.write",
            arguments={"sku": "42"},
            context={"session_id": "task-a", "agent_id": "7"},
            identity={"actor": "agent:revoked"},
        )
        assert not decision.allowed
        assert decision.violation.value == "principal_revoked"

    async def test_off_mode_still_denies_via_port(self) -> None:
        service = GovernanceService(
            TaskScopedProfile(allowed_tasks={"task-a"}),
            revocation=_RevokedView(),
        )
        registry = _RegistryStub()
        port = _ProductionRuntimePort(
            db=None,
            llm_service=None,
            tool_registry=registry,
            governance=PlatformGovernance(service, GovernanceMode.OFF),
        )
        with pytest.raises(GovernanceDeniedError, match="revoked"):
            # The engine-path actor derives from the call context's agent_id.
            await port.tool_execute(
                "inventory.write",
                {"sku": "42"},
                {"agent_id": "revoked", "session_id": "task-a"},
            )


class TestFactoryWiring:
    async def test_factory_builds_governance_by_default(self) -> None:
        from hecate.core.composition.runtime_port_adapter import create_runtime_port

        port = create_runtime_port(db=None, llm_service=None, tool_registry=None)
        # The assembled port carries a governance facade (audit default).
        assert port._governance is not None
        assert port._governance.mode is GovernanceMode.AUDIT
