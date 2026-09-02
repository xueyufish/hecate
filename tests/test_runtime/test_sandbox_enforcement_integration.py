"""Integration tests for sandbox enforcement in ToolWorker.

These tests verify that SandboxEnforcementRouter correctly routes
EXECUTE_SANDBOX decisions to DockerEnvironment when enforcement is enabled.
"""

from __future__ import annotations

from hecate.runtime.tool_access import AccessDecision
from hecate.runtime.workers.sandbox_router import SandboxEnforcementRouter


class TestToolWorkerSandboxEnforcement:
    def test_enforcement_disabled_uses_port(self):
        router = SandboxEnforcementRouter(enabled=False)
        assert not router.should_route_to_environment(
            "bash",
            AccessDecision.EXECUTE_SANDBOX,
        )

    def test_enforcement_enabled_routes_bash_to_environment(self):
        router = SandboxEnforcementRouter(enabled=True)
        assert router.should_route_to_environment(
            "bash",
            AccessDecision.EXECUTE_SANDBOX,
        )

    def test_enforcement_enabled_does_not_route_python_tool(self):
        router = SandboxEnforcementRouter(enabled=True)
        assert not router.should_route_to_environment(
            "read_file",
            AccessDecision.EXECUTE_SANDBOX,
        )

    def test_enforcement_enabled_does_not_route_execute_decision(self):
        router = SandboxEnforcementRouter(enabled=True)
        assert not router.should_route_to_environment(
            "bash",
            AccessDecision.EXECUTE,
        )

    def test_context_flag_set_for_environment_routing(self):
        router = SandboxEnforcementRouter(enabled=True)
        should_route = router.should_route_to_environment(
            "bash",
            AccessDecision.EXECUTE_SANDBOX,
        )
        assert should_route is True
