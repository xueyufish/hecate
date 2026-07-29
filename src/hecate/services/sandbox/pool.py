"""Sandbox pool for managing reusable Docker containers.

Pre-warms, allocates, recycles, and retires Docker containers
to amortize startup cost across multiple tool executions.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Literal

from hecate.services.sandbox.executor import SandboxConfig, SandboxExecutor, SandboxResult

logger = logging.getLogger(__name__)


class PoolExhaustedError(Exception):
    """Raised when the sandbox pool is exhausted and acquisition times out."""


@dataclass
class PooledContainer:
    """A container in the sandbox pool."""

    container_id: str
    use_count: int = 0
    in_use: bool = False
    allocated_at: float = field(default_factory=time.monotonic)
    last_used_at: float = field(default_factory=time.monotonic)


class SandboxPool:
    """Manages a pool of reusable Docker containers for sandboxed tool execution.

    Provides:
    - Pre-warming containers on startup
    - Allocation from pool or on-demand creation
    - Health check on acquire
    - Recycling containers after use (clean and return to pool)
    - Max-uses policy — destroy after N uses to prevent state leakage
    - TTL busy marker for crash recovery
    - Idle trimming for resource efficiency
    - Exhaustion strategy (wait / temporary)
    """

    def __init__(
        self,
        executor: SandboxExecutor | None = None,
        pool_size: int = 3,
        max_uses: int = 50,
        busy_ttl: int = 1800,
        idle_timeout: int = 300,
        acquire_timeout: int = 30,
        exhaustion_strategy: Literal["wait", "temporary"] = "wait",
    ) -> None:
        self._executor = executor or SandboxExecutor()
        self._pool_size = pool_size
        self._max_uses = max_uses
        self._busy_ttl = busy_ttl
        self._idle_timeout = idle_timeout
        self._acquire_timeout = acquire_timeout
        self._exhaustion_strategy = exhaustion_strategy
        self._pool: list[PooledContainer] = []
        self._lock = asyncio.Lock()
        self._available_event = asyncio.Event()
        self._reaper_task: asyncio.Task[None] | None = None
        self._trimmer_task: asyncio.Task[None] | None = None

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

        self._reaper_task = asyncio.create_task(self._reap_loop())
        self._trimmer_task = asyncio.create_task(self._trim_loop())

    async def allocate(self) -> PooledContainer:
        """Get a sandbox from the pool, with health check and exhaustion strategy.

        Returns:
            A PooledContainer ready for use.

        Raises:
            PoolExhaustedError: If wait strategy times out.
        """
        async with self._lock:
            container = await self._try_allocate()
            if container:
                return container

        # Pool exhausted — apply strategy
        if self._exhaustion_strategy == "temporary":
            return await self._allocate_temporary()

        return await self._allocate_wait()

    async def _try_allocate(self) -> PooledContainer | None:
        """Try to allocate an idle container. Must hold self._lock."""
        for container in self._pool:
            if not container.in_use:
                if await self._health_check(container.container_id):
                    container.in_use = True
                    container.use_count += 1
                    container.allocated_at = time.monotonic()
                    logger.debug(f"Allocated existing container {container.container_id[:12]}")
                    return container
                # Dead container — discard
                logger.warning(f"Discarding dead container {container.container_id[:12]}")
                await self._destroy_container(container)

        # Pool below capacity — create new
        if len(self._pool) < self._pool_size:
            container_id = await self._create_fresh_container()
            container = PooledContainer(container_id=container_id, use_count=1, in_use=True)
            self._pool.append(container)
            logger.debug(f"Created new container {container_id[:12]} (below capacity)")
            return container

        return None

    async def _allocate_wait(self) -> PooledContainer:
        """Wait strategy: block until a container becomes available."""
        deadline = time.monotonic() + self._acquire_timeout
        self._available_event.clear()

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PoolExhaustedError(
                    f"Sandbox pool exhausted after {self._acquire_timeout}s timeout "
                    f"(pool_size={self._pool_size}, total={self.total_count}, "
                    f"available={self.available_count})"
                )

            try:
                await asyncio.wait_for(self._available_event.wait(), timeout=remaining)
            except TimeoutError:
                raise PoolExhaustedError(
                    f"Sandbox pool exhausted after {self._acquire_timeout}s timeout "
                    f"(pool_size={self._pool_size}, total={self.total_count}, "
                    f"available={self.available_count})"
                ) from None

            self._available_event.clear()

            async with self._lock:
                container = await self._try_allocate()
                if container:
                    return container

    async def _allocate_temporary(self) -> PooledContainer:
        """Temporary strategy: create ephemeral container outside pool."""
        container_id = await self._create_fresh_container()
        container = PooledContainer(container_id=container_id, use_count=1, in_use=True)
        self._pool.append(container)
        logger.debug(f"Created temporary container {container_id[:12]} (pool exhausted)")
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
                logger.info(f"Retired container {container.container_id[:12]} after {container.use_count} uses")
                return

            try:
                await self._clean_container(container.container_id)
                container.in_use = False
                container.last_used_at = time.monotonic()
                self._available_event.set()
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

        Allocates a container, runs the tool inside it via docker exec,
        and recycles the container.

        Args:
            tool_name: Tool to execute.
            args: Tool arguments.
            config: Optional sandbox config.

        Returns:
            SandboxResult with execution output.
        """
        container = await self.allocate()
        try:
            return await self._executor.execute(tool_name, args, config, container_id=container.container_id)
        finally:
            await self.recycle(container)

    async def shutdown(self) -> None:
        """Destroy all containers in the pool and stop background tasks."""
        if self._reaper_task and not self._reaper_task.done():
            self._reaper_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reaper_task

        if self._trimmer_task and not self._trimmer_task.done():
            self._trimmer_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._trimmer_task

        async with self._lock:
            for container in self._pool:
                try:
                    await self._destroy_container(container)
                except Exception as e:
                    logger.warning(f"Error destroying container during shutdown: {e}")
            self._pool.clear()
            logger.info("Sandbox pool shut down")

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    @staticmethod
    async def _health_check(container_id: str) -> bool:
        """Check if a container is alive via ``docker exec <id> true``."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "exec",
                container_id,
                "true",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.communicate(), timeout=5)
            return proc.returncode == 0
        except (TimeoutError, OSError):
            return False

    # ------------------------------------------------------------------
    # Background tasks
    # ------------------------------------------------------------------

    async def _reap_loop(self) -> None:
        """Periodically release containers stuck in_use beyond busy_ttl."""
        while True:
            await asyncio.sleep(60)
            await self._reap_stale_containers()

    async def _reap_stale_containers(self) -> None:
        """Force-release containers in_use longer than busy_ttl."""
        now = time.monotonic()
        async with self._lock:
            for container in self._pool:
                if container.in_use and (now - container.allocated_at) > self._busy_ttl:
                    logger.warning(
                        f"Reaping stale container {container.container_id[:12]} "
                        f"(in_use for {now - container.allocated_at:.0f}s > {self._busy_ttl}s)"
                    )
                    try:
                        await self._clean_container(container.container_id)
                        container.in_use = False
                        container.last_used_at = now
                    except Exception as e:
                        logger.warning(f"Failed to clean stale container {container.container_id[:12]}: {e}")
                        await self._destroy_container(container)
            self._available_event.set()

    async def _trim_loop(self) -> None:
        """Periodically destroy excess idle containers beyond pool_size."""
        while True:
            await asyncio.sleep(60)
            await self._trim_idle_containers()

    async def _trim_idle_containers(self) -> None:
        """Destroy excess idle containers older than idle_timeout."""
        now = time.monotonic()
        async with self._lock:
            idle = [c for c in self._pool if not c.in_use]
            excess = [c for c in idle if (now - c.last_used_at) > self._idle_timeout]
            excess = excess[self._pool_size :]  # Keep pool_size idle containers

            for container in excess:
                logger.debug(f"Trimming idle container {container.container_id[:12]}")
                await self._destroy_container(container)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _create_fresh_container(self) -> str:
        """Create a new Docker container via docker run --detach.

        Returns:
            Container ID string.
        """
        cfg = self._executor.config
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
            "docker",
            "exec",
            container_id,
            "sh",
            "-c",
            "rm -rf /tmp/* 2>/dev/null || true",
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
                "docker",
                "rm",
                "-f",
                container.container_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        finally:
            if container in self._pool:
                self._pool.remove(container)
