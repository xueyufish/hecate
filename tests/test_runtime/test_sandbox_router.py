"""Tests for SandboxEnforcementRouter (engine/workers/sandbox_router.py)."""

from __future__ import annotations

from hecate.runtime.tool_access import AccessDecision
from hecate.runtime.workers.sandbox_router import (
    SandboxEnforcementRouter,
    is_shell_tool,
)


class TestIsShellTool:
    def test_bash_is_shell(self):
        assert is_shell_tool("bash") is True

    def test_exec_shell_is_shell(self):
        assert is_shell_tool("exec_shell") is True

    def test_execute_code_is_shell(self):
        assert is_shell_tool("execute_code") is True

    def test_read_file_is_not_shell(self):
        assert is_shell_tool("read_file") is False

    def test_case_insensitive(self):
        assert is_shell_tool("BASH") is True

    def test_unknown_tool_is_not_shell(self):
        assert is_shell_tool("my_custom_tool") is False


class TestSandboxEnforcementRouter:
    def test_disabled_router_never_routes(self):
        router = SandboxEnforcementRouter(enabled=False)
        assert not router.should_route_to_environment(
            "bash",
            AccessDecision.EXECUTE_SANDBOX,
        )

    def test_enabled_shell_tool_with_sandbox_decision(self):
        router = SandboxEnforcementRouter(enabled=True)
        assert (
            router.should_route_to_environment(
                "bash",
                AccessDecision.EXECUTE_SANDBOX,
            )
            is True
        )

    def test_enabled_shell_tool_with_execute_decision(self):
        router = SandboxEnforcementRouter(enabled=True)
        assert (
            router.should_route_to_environment(
                "bash",
                AccessDecision.EXECUTE,
            )
            is False
        )

    def test_python_tool_not_routed(self):
        router = SandboxEnforcementRouter(enabled=True)
        assert (
            router.should_route_to_environment(
                "read_file",
                AccessDecision.EXECUTE_SANDBOX,
            )
            is False
        )

    def test_mcp_sandboxed_tool_routed(self):
        router = SandboxEnforcementRouter(enabled=True)
        assert (
            router.should_route_to_environment(
                "mcp_custom_tool",
                AccessDecision.EXECUTE_SANDBOX,
                sandbox_enabled=True,
            )
            is True
        )

    def test_deny_decision_not_routed(self):
        router = SandboxEnforcementRouter(enabled=True)
        assert (
            router.should_route_to_environment(
                "bash",
                AccessDecision.DENY,
            )
            is False
        )

    def test_require_approval_not_routed(self):
        router = SandboxEnforcementRouter(enabled=True)
        assert (
            router.should_route_to_environment(
                "bash",
                AccessDecision.REQUIRE_APPROVAL,
            )
            is False
        )

    def test_verify_normal_exit(self):
        router = SandboxEnforcementRouter()
        assert router.verify_container_exit(0, b"") is True

    def test_verify_abnormal_exit_negative_code(self):
        router = SandboxEnforcementRouter()
        assert router.verify_container_exit(-1, b"OOM killed") is False

    def test_verify_abnormal_exit_signal(self):
        router = SandboxEnforcementRouter()
        assert router.verify_container_exit(-9, b"killed") is False
