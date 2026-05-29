"""Sandbox executor for running tools in isolated Docker containers.

Provides resource-constrained, timeout-bounded execution of tool calls
inside Docker containers for security isolation.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_IMAGE = "hecate-sandbox:latest"
_DEFAULT_TIMEOUT = 30
_DEFAULT_CPU_PERIOD = 100_000
_DEFAULT_CPU_QUOTA = 50_000
_DEFAULT_MEMORY = "128m"
_DEFAULT_NETWORK_MODE = "none"


@dataclass
class SandboxConfig:
    """Configuration for a sandbox execution environment."""

    image: str = _DEFAULT_IMAGE
    cpu_period: int = _DEFAULT_CPU_PERIOD
    cpu_quota: int = _DEFAULT_CPU_QUOTA
    memory_limit: str = _DEFAULT_MEMORY
    network_mode: str = _DEFAULT_NETWORK_MODE
    timeout_seconds: int = _DEFAULT_TIMEOUT
    read_only_fs: bool = True
    env_vars: dict[str, str] = field(default_factory=dict)


@dataclass
class SandboxResult:
    """Result of a sandbox execution."""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


class SandboxExecutor:
    """Executes tool calls inside Docker containers with resource limits.

    Provides:
    - Container-based isolation for tool execution
    - CPU, memory, network, and filesystem resource limits
    - Timeout handling with container destruction
    """

    def __init__(self, config: SandboxConfig | None = None) -> None:
        self.config = config or SandboxConfig()

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        config: SandboxConfig | None = None,
    ) -> SandboxResult:
        """Execute a tool inside a Docker container.

        Args:
            tool_name: Name of the tool to execute.
            args: Arguments to pass to the tool.
            config: Optional per-execution config override.

        Returns:
            SandboxResult with exit code, stdout, and stderr.
        """
        cfg = config or self.config
        container_id = None

        try:
            container_id = await self._create_container(tool_name, args, cfg)
            return await self._wait_container(container_id, cfg.timeout_seconds)
        except TimeoutError:
            if container_id:
                await self._destroy_container(container_id)
            return SandboxResult(exit_code=-1, stdout="", stderr="Execution timed out", timed_out=True)
        except Exception as e:
            logger.error(f"Sandbox execution failed for {tool_name}: {e}")
            return SandboxResult(exit_code=-1, stdout="", stderr=str(e))
        finally:
            if container_id:
                await self._destroy_container(container_id)

    async def _create_container(
        self,
        tool_name: str,
        args: dict[str, Any],
        cfg: SandboxConfig,
    ) -> str:
        """Create and start a Docker container for tool execution.

        Args:
            tool_name: Tool to execute.
            args: Tool arguments.
            cfg: Sandbox configuration.

        Returns:
            Container ID.
        """
        cmd_args = json.dumps({"tool": tool_name, "args": args})

        docker_args = [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--cpu-period",
            str(cfg.cpu_period),
            "--cpu-quota",
            str(cfg.cpu_quota),
            "--memory",
            cfg.memory_limit,
            "--network",
            cfg.network_mode,
            "--env",
            f"TOOL_INPUT={cmd_args}",
        ]

        if cfg.read_only_fs:
            docker_args.append("--read-only")

        for key, val in cfg.env_vars.items():
            docker_args.extend(["--env", f"{key}={val}"])

        docker_args.append(cfg.image)

        proc = await asyncio.create_subprocess_exec(
            *docker_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)

        if proc.returncode != 0:
            raise RuntimeError(f"Failed to create container for {tool_name}")

        container_id = stdout.decode().strip()
        logger.debug(f"Created sandbox container {container_id[:12]} for tool {tool_name}")
        return container_id

    async def _wait_container(self, container_id: str, timeout: int) -> SandboxResult:
        """Wait for container to finish and collect output.

        Args:
            container_id: Container to wait for.
            timeout: Maximum seconds to wait.

        Returns:
            SandboxResult with execution output.

        Raises:
            TimeoutError: If execution exceeds timeout.
        """
        wait_proc = await asyncio.create_subprocess_exec(
            "docker",
            "wait",
            container_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, _ = await asyncio.wait_for(wait_proc.communicate(), timeout=timeout)
        except TimeoutError:
            raise TimeoutError(f"Container {container_id[:12]} timed out after {timeout}s") from None

        exit_code = int(stdout.decode().strip()) if stdout else -1

        logs_proc = await asyncio.create_subprocess_exec(
            "docker",
            "logs",
            container_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        log_stdout, log_stderr = await logs_proc.communicate()

        return SandboxResult(
            exit_code=exit_code,
            stdout=log_stdout.decode(errors="replace"),
            stderr=log_stderr.decode(errors="replace"),
        )

    async def _destroy_container(self, container_id: str) -> None:
        """Force-destroy a running container.

        Args:
            container_id: Container to destroy.
        """
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "rm",
            "-f",
            container_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        logger.debug(f"Destroyed sandbox container {container_id[:12]}")
