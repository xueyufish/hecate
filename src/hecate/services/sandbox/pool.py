"""Sandbox pool for managing reusable Docker containers.

Pre-warms, allocates, recycles, and retires Docker containers
to amortize startup cost across multiple tool executions.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from hecate.services.sandbox.executor import SandboxConfig, SandboxExecutor, SandboxResult

logger = logging.getLogger(__name__)


@dataclass
class PooledContainer:
    """A container in the sandbox pool."""

    container_id: str
    use_count: int = 0
    in_use: bool = False


class SandboxPool:
    """Manages a pool of reusable Docker containers for sandboxed tool execution.

    Provides:
    - Pre-warming containers on startup
    - Allocation from pool or on-demand creation
    - Recycling containers after use (clean and return to pool)
    - Max-uses policy — destroy after N uses to prevent state leakage
    """

    def __init__(
        self,
        executor: SandboxExecutor | None = None,
        pool_size: int = 3,
        max_uses: int = 50,
    ) -> None:
        self._executor = executor or SandboxExecutor()
        self._pool_size = pool_size
        self._max_uses = max_uses
        self._pool: list[PooledContainer] = []
        self._lock = asyncio.Lock()

    @property
    def available_count(self) -> int:
        """Number of idle containers in pool."""
        return sum(1 for c in self._pool if not c.in_use)

    @property
    def total_count(self) -> int:
        """Total containers in pool (including in-use)."""
        return len(self._pool)

    async def prewarm(self) -> None:
        """Create N containers upfront to eliminate cold-start latency."""
        for i in range(self._pool_size):
            try:
                container_id = await self._create_fresh_container()
                self._pool.append(PooledContainer(container_id=container_id))
                logger.debug(f"Pre-warmed sandbox container {i + 1}/{self._pool_size}: {container_id[:12]}")
            except Exception as e:
                logger.warning(f"Failed to pre-warm container {i + 1}: {e}")

    async def allocate(self) -> PooledContainer:
        """Get a sandbox from the pool or create a new one.

        Returns:
            A PooledContainer ready for use.
        """
        async with self._lock:
            for container in self._pool:
                if not container.in_use:
                    container.in_use = True
                    container.use_count += 1
                    logger.debug(f"Allocated existing container {container.container_id[:12]}")
                    return container

            container_id = await self._create_fresh_container()
            container = PooledContainer(container_id=container_id, use_count=1, in_use=True)
            self._pool.append(container)
            logger.debug(f"Created new container {container_id[:12]} (pool exhausted)")
            return container

    async def recycle(self, container: PooledContainer) -> None:
        """Clean and return a container to the pool.

        If the container has exceeded max uses, it is destroyed instead.

        Args:
            container: The container to recycle.
        """
        async with self._lock:
            if container.use_count >= self._max_uses:
                await self._destroy_container(container)
                logger.info(
                    f"Retired container {container.container_id[:12]} "
                    f"after {container.use_count} uses"
                )
                return

            try:
                await self._clean_container(container.container_id)
                container.in_use = False
                logger.debug(f"Recycled container {container.container_id[:12]}")
            except Exception as e:
                logger.warning(f"Failed to recycle container {container.container_id[:12]}: {e}")
                await self._destroy_container(container)

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        config: SandboxConfig | None = None,
    ) -> SandboxResult:
        """Execute a tool using a pooled container.

        Allocates a container, runs the tool, and recycles the container.

        Args:
            tool_name: Tool to execute.
            args: Tool arguments.
            config: Optional sandbox config.

        Returns:
            SandboxResult with execution output.
        """
        container = await self.allocate()
        try:
            return await self._executor.execute(tool_name, args, config)
        finally:
            await self.recycle(container)

    async def shutdown(self) -> None:
        """Destroy all containers in the pool."""
        async with self._lock:
            for container in self._pool:
                try:
                    await self._destroy_container(container)
                except Exception as e:
                    logger.warning(f"Error destroying container during shutdown: {e}")
            self._pool.clear()
            logger.info("Sandbox pool shut down")

    async def _create_fresh_container(self) -> str:
        """Create a new Docker container via docker run --detach.

        Returns:
            Container ID string.
        """
        cfg = self._executor.config
        docker_args = [
            "docker", "run",
            "--detach",
            "--rm",
            "--cpu-period", str(cfg.cpu_period),
            "--cpu-quota", str(cfg.cpu_quota),
            "--memory", cfg.memory_limit,
            "--network", cfg.network_mode,
        ]
        if cfg.read_only_fs:
            docker_args.append("--read-only")

        docker_args.extend(["--entrypoint", "sleep"])
        docker_args.append(cfg.image)
        docker_args.append("infinity")

        proc = await asyncio.create_subprocess_exec(
            *docker_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError("Failed to create sandbox container")

        return stdout.decode().strip()

    async def _clean_container(self, container_id: str) -> None:
        """Reset container state by removing temporary files.

        Args:
            container_id: Container to clean.
        """
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", container_id,
            "sh", "-c", "rm -rf /tmp/* 2>/dev/null || true",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    async def _destroy_container(self, container: PooledContainer) -> None:
        """Remove a container from the pool and destroy it.

        Args:
            container: Container to destroy.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", container.container_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        finally:
            if container in self._pool:
                self._pool.remove(container)
