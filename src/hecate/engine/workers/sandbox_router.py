"""Sandbox enforcement router for ToolWorker.

When ``AGENT_ENV_SANDBOX_ENFORCEMENT=true``, routes ``EXECUTE_SANDBOX``
decisions for shell/exec tools to ``DockerEnvironment.exec_shell()``
instead of the generic ``port.tool_execute_sandbox()``. Provides container
exit verification and anomaly audit emission.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from hecate.engine.audit_sink import audit_emitter
from hecate.engine.tool_access import AccessDecision

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Tool name patterns that are shell/exec tools (should route to container).
_SHELL_TOOL_PATTERNS: tuple[str, ...] = (
    "bash",
    "exec_shell",
    "execute_code",
    "shell",
    "command",
    "run_command",
)


def is_shell_tool(tool_name: str) -> bool:
    """Check if a tool is a shell/exec type that should route to container.

    Args:
        tool_name: The tool name to check.

    Returns:
        True if the tool is a shell/exec type.
    """
    return tool_name.lower() in _SHELL_TOOL_PATTERNS


class SandboxEnforcementRouter:
    """Routes EXECUTE_SANDBOX decisions based on tool type and enforcement config.

    When enforcement is enabled, shell/exec tools with EXECUTE_SANDBOX
    decision are routed to DockerEnvironment for container-isolated
    execution. When disabled (default), EXECUTE_SANDBOX is handled by
    the existing ``port.tool_execute_sandbox()`` path (backward compatible).

    Args:
        enabled: Whether sandbox enforcement is active. Controlled by
            ``AGENT_ENV_SANDBOX_ENFORCEMENT`` setting.
    """

    def __init__(self, enabled: bool = False) -> None:
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        """Whether sandbox enforcement is active."""
        return self._enabled

    def should_route_to_environment(
        self,
        tool_name: str,
        decision: AccessDecision,
        sandbox_enabled: bool = False,
    ) -> bool:
        """Determine if a tool should execute inside DockerEnvironment.

        Args:
            tool_name: The tool being executed.
            decision: The AccessDecision from ToolAccessPolicy.
            sandbox_enabled: Whether the tool has sandbox_enabled metadata.

        Returns:
            True if the tool should route to DockerEnvironment.exec_shell().
        """
        if not self._enabled:
            return False

        if decision != AccessDecision.EXECUTE_SANDBOX:
            return False

        # Shell/exec tools route to container
        if is_shell_tool(tool_name):
            return True

        # MCP tools with sandbox_enabled also route to container
        return sandbox_enabled

    def verify_container_exit(self, exit_code: int, stderr: bytes) -> bool:
        """Verify container health after sandboxed execution.

        Args:
            exit_code: Process exit code from exec_shell().
            stderr: Stderr output from exec_shell().

        Returns:
            True if container appears healthy, False if anomaly detected.
        """
        # Exit code -1 from ExecResult indicates timeout or transport error.
        # Negative exit codes may indicate signals (OOM killer, segfault).
        if exit_code < 0:
            audit_emitter.emit(
                audit_emitter.build_event(
                    agent_id=None,
                    workspace_id=None,
                    tool_name="sandbox_verification",
                    decision="sandbox_anomaly",
                    reason=f"Abnormal container exit: code={exit_code}, stderr={stderr[:200].decode(errors='replace')}",
                )
            )
            logger.warning(
                "Sandbox anomaly detected: exit_code=%d, stderr=%s",
                exit_code,
                stderr[:200],
            )
            return False

        return True
