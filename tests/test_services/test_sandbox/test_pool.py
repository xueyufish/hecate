"""Unit tests for SandboxPool and SandboxExecutor docker exec support."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hecate.services.sandbox.executor import SandboxConfig, SandboxExecutor, SandboxResult
from hecate.services.sandbox.pool import PooledContainer, PoolExhaustedError, SandboxPool

# ---------------------------------------------------------------------------
# TestPooledContainer
# ---------------------------------------------------------------------------


class TestPooledContainer:
    def test_init_defaults(self) -> None:
        c = PooledContainer(container_id="abc123")

        assert c.container_id == "abc123"
        assert c.use_count == 0
        assert c.in_use is False
        assert c.allocated_at > 0
        assert c.last_used_at > 0


# ---------------------------------------------------------------------------
# TestSandboxExecutor (task 1.5)
# ---------------------------------------------------------------------------


class TestSandboxExecutor:
    async def test_execute_with_container_id_uses_docker_exec(self) -> None:
        """Task 5.11: execute() with container_id uses docker exec path."""
        executor = SandboxExecutor()
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"hello", b""))
        mock_proc.returncode = 0
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        with (
            patch(
                "hecate.services.sandbox.executor.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=mock_proc,
            ),
            patch(
                "hecate.services.sandbox.executor.asyncio.wait_for",
                new_callable=AsyncMock,
                return_value=(b"hello", b""),
            ),
        ):
            result = await executor.execute("execute_code", {"code": "print(1)"}, container_id="c123")

        assert result.exit_code == 0
        assert result.stdout == "hello"

    async def test_execute_without_container_id_uses_docker_run(self) -> None:
        """Task 5.12: execute() without container_id uses docker run path."""
        executor = SandboxExecutor()
        mock_create = MagicMock()
        mock_create.communicate = AsyncMock(return_value=(b"c123", b""))
        mock_create.returncode = 0

        mock_wait = MagicMock()
        mock_wait.communicate = AsyncMock(return_value=(b"0", b""))
        mock_wait.returncode = 0

        mock_logs = MagicMock()
        mock_logs.communicate = AsyncMock(return_value=(b"output", b""))
        mock_logs.returncode = 0

        mock_rm = MagicMock()
        mock_rm.communicate = AsyncMock(return_value=(b"", b""))
        mock_rm.returncode = 0

        with (
            patch(
                "hecate.services.sandbox.executor.asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec,
            patch("hecate.services.sandbox.executor.asyncio.wait_for", new_callable=AsyncMock) as mock_wait_for,
        ):
            mock_exec.side_effect = [mock_create, mock_wait, mock_logs, mock_rm]
            mock_wait_for.side_effect = [(b"c123", b""), (b"0", b"")]
            result = await executor.execute("execute_code", {"code": "print(1)"})

        assert result.exit_code == 0
        assert result.stdout == "output"


# ---------------------------------------------------------------------------
# TestSandboxPool
# ---------------------------------------------------------------------------


class TestSandboxPool:
    def test_initial_state(self) -> None:
        pool = SandboxPool(pool_size=5)

        assert pool.available_count == 0
        assert pool.total_count == 0

    async def test_prewarm(self) -> None:
        pool = SandboxPool(pool_size=3)

        with patch.object(pool, "_create_fresh_container", new_callable=AsyncMock) as mock_create:
            mock_create.side_effect = ["c1", "c2", "c3"]
            await pool.prewarm()

            assert pool.total_count == 3
            assert pool.available_count == 3

    async def test_prewarm_partial_failure(self) -> None:
        pool = SandboxPool(pool_size=3)

        with patch.object(pool, "_create_fresh_container", new_callable=AsyncMock) as mock_create:
            mock_create.side_effect = ["c1", RuntimeError("fail"), "c3"]
            await pool.prewarm()

            assert pool.total_count == 2

    async def test_allocate_existing_healthy(self) -> None:
        """Task 5.3: Health check on acquire with healthy container passes through."""
        pool = SandboxPool(pool_size=2)

        with (
            patch.object(pool, "_create_fresh_container", new_callable=AsyncMock) as mock_create,
            patch.object(SandboxPool, "_health_check", new_callable=AsyncMock, return_value=True),
        ):
            mock_create.side_effect = ["c1", "c2"]
            await pool.prewarm()

            container = await pool.allocate()

            assert container.in_use is True
            assert container.use_count == 1
            assert pool.available_count == 1

    async def test_allocate_detects_dead_container(self) -> None:
        """Task 5.2: Health check on acquire detects dead container and replaces it."""
        pool = SandboxPool(pool_size=1)

        async def mock_destroy(container: PooledContainer) -> None:
            if container in pool._pool:
                pool._pool.remove(container)

        with (
            patch.object(pool, "_create_fresh_container", new_callable=AsyncMock) as mock_create,
            patch.object(SandboxPool, "_health_check", new_callable=AsyncMock) as mock_health,
            patch.object(pool, "_destroy_container", side_effect=mock_destroy),
        ):
            mock_create.side_effect = ["c1", "c2"]
            mock_health.side_effect = [False, True]
            await pool.prewarm()

            container = await pool.allocate()

            assert container.container_id == "c2"
            assert container.in_use is True

    async def test_recycle_returns_to_pool(self) -> None:
        pool = SandboxPool(pool_size=1)

        with (
            patch.object(pool, "_create_fresh_container", new_callable=AsyncMock) as mock_create,
            patch.object(pool, "_clean_container", new_callable=AsyncMock),
        ):
            mock_create.return_value = "c1"
            await pool.prewarm()
            container = await pool.allocate()

            assert pool.available_count == 0

            await pool.recycle(container)

            assert container.in_use is False
            assert pool.available_count == 1

    async def test_recycle_destroys_at_max_uses(self) -> None:
        """Task 5.9: Recycle destroys container at max_uses."""
        pool = SandboxPool(pool_size=1, max_uses=2)

        with (
            patch.object(pool, "_create_fresh_container", new_callable=AsyncMock) as mock_create,
            patch("hecate.services.sandbox.pool.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec,
        ):
            mock_create.return_value = "c1"
            await pool.prewarm()
            container = await pool.allocate()

            mock_exec.return_value = AsyncMock(
                communicate=AsyncMock(return_value=(b"", b"")),
                returncode=0,
            )
            container.use_count = 2
            await pool.recycle(container)

            assert pool.total_count == 0

    async def test_recycle_failure_destroys_container(self) -> None:
        """Task 5.10: Recycle failure destroys container (prevents state contamination)."""
        pool = SandboxPool(pool_size=1)

        with (
            patch.object(pool, "_create_fresh_container", new_callable=AsyncMock) as mock_create,
            patch.object(SandboxPool, "_health_check", new_callable=AsyncMock, return_value=True),
            patch.object(pool, "_clean_container", new_callable=AsyncMock, side_effect=RuntimeError("clean failed")),
            patch.object(pool, "_destroy_container", new_callable=AsyncMock) as mock_destroy,
        ):
            mock_create.return_value = "c1"
            await pool.prewarm()
            container = await pool.allocate()

            await pool.recycle(container)

            mock_destroy.assert_called_once_with(container)

    async def test_execute_passes_container_id(self) -> None:
        """Task 5.1: Fix test_execute_delegates_to_executor — verify container_id is passed."""
        pool = SandboxPool(pool_size=1)
        pool._executor = AsyncMock()

        with (
            patch.object(pool, "_create_fresh_container", new_callable=AsyncMock) as mock_create,
            patch.object(pool, "_clean_container", new_callable=AsyncMock),
            patch.object(SandboxPool, "_health_check", new_callable=AsyncMock, return_value=True),
        ):
            mock_create.return_value = "c1"
            pool._executor.execute = AsyncMock(return_value=SandboxResult(exit_code=0, stdout="result", stderr=""))
            pool._executor.config = SandboxConfig()

            result = await pool.execute("tool", {"arg": "val"})

            assert result.exit_code == 0
            assert result.stdout == "result"
            # Verify container_id was passed
            pool._executor.execute.assert_called_once_with("tool", {"arg": "val"}, None, container_id="c1")

    async def test_wait_exhaustion_blocks_then_succeeds(self) -> None:
        """Task 5.6: WAIT exhaustion strategy blocks then succeeds within timeout."""
        pool = SandboxPool(pool_size=1, acquire_timeout=5, exhaustion_strategy="wait")

        with (
            patch.object(pool, "_create_fresh_container", new_callable=AsyncMock) as mock_create,
            patch.object(SandboxPool, "_health_check", new_callable=AsyncMock, return_value=True),
            patch.object(pool, "_clean_container", new_callable=AsyncMock),
        ):
            mock_create.return_value = "c1"
            await pool.prewarm()

            # Allocate the only container
            await pool.allocate()

            # Schedule a recycle after a short delay so the wait succeeds
            async def delayed_recycle() -> None:
                await asyncio.sleep(0.1)
                await pool.recycle(pool._pool[0])

            task = asyncio.create_task(delayed_recycle())

            # This should block until recycle releases the container
            container = await pool.allocate()
            assert container.container_id == "c1"

            await task

    async def test_wait_exhaustion_raises_on_timeout(self) -> None:
        """Task 5.7: WAIT exhaustion strategy raises PoolExhaustedError on timeout."""
        pool = SandboxPool(pool_size=1, acquire_timeout=1, exhaustion_strategy="wait")

        with (
            patch.object(pool, "_create_fresh_container", new_callable=AsyncMock) as mock_create,
            patch.object(SandboxPool, "_health_check", new_callable=AsyncMock, return_value=True),
        ):
            mock_create.return_value = "c1"
            await pool.prewarm()

            await pool.allocate()

            with pytest.raises(PoolExhaustedError):
                await pool.allocate()

    async def test_temporary_strategy_creates_ephemeral(self) -> None:
        """Task 5.8: TEMPORARY exhaustion strategy creates ephemeral container."""
        pool = SandboxPool(pool_size=1, exhaustion_strategy="temporary")

        with (
            patch.object(pool, "_create_fresh_container", new_callable=AsyncMock) as mock_create,
            patch.object(SandboxPool, "_health_check", new_callable=AsyncMock, return_value=True),
            patch.object(pool, "_clean_container", new_callable=AsyncMock),
        ):
            mock_create.side_effect = ["c1", "c2"]
            await pool.prewarm()

            await pool.allocate()  # uses c1
            container2 = await pool.allocate()  # creates temporary c2

            assert container2.container_id == "c2"
            assert pool.total_count == 2

    async def test_ttl_busy_marker_reaps_stale(self) -> None:
        """Task 5.4: TTL busy marker reaps stale in_use container."""
        pool = SandboxPool(pool_size=1, busy_ttl=1)

        with (
            patch.object(pool, "_create_fresh_container", new_callable=AsyncMock) as mock_create,
            patch.object(SandboxPool, "_health_check", new_callable=AsyncMock, return_value=True),
            patch.object(pool, "_clean_container", new_callable=AsyncMock),
        ):
            mock_create.return_value = "c1"
            await pool.prewarm()

            container = await pool.allocate()
            # Simulate stale container by setting allocated_at far in the past
            container.allocated_at = time.monotonic() - 10

            await pool._reap_stale_containers()

            assert container.in_use is False

    async def test_idle_trimming_destroys_excess(self) -> None:
        """Task 5.5: Idle trimming destroys excess idle containers after timeout."""
        pool = SandboxPool(pool_size=2, idle_timeout=1)

        with (
            patch.object(pool, "_create_fresh_container", new_callable=AsyncMock) as mock_create,
            patch.object(SandboxPool, "_health_check", new_callable=AsyncMock, return_value=True),
            patch.object(pool, "_clean_container", new_callable=AsyncMock),
            patch.object(pool, "_destroy_container", new_callable=AsyncMock) as mock_destroy,
        ):
            mock_create.side_effect = ["c1", "c2", "c3"]
            await pool.prewarm()

            # Create a third container (exceeds pool_size)
            await pool._create_fresh_container()
            pool._pool.append(PooledContainer(container_id="c3"))

            # Simulate old idle time for excess containers
            for c in pool._pool:
                c.in_use = False
                c.last_used_at = time.monotonic() - 10

            await pool._trim_idle_containers()

            # Should have trimmed excess beyond pool_size
            assert mock_destroy.called

    async def test_shutdown(self) -> None:
        """Task 5.14: Graceful shutdown destroys all containers."""
        pool = SandboxPool(pool_size=2)

        with (
            patch.object(pool, "_create_fresh_container", new_callable=AsyncMock) as mock_create,
            patch.object(pool, "_destroy_container", new_callable=AsyncMock) as mock_destroy,
        ):
            mock_create.side_effect = ["c1", "c2"]
            await pool.prewarm()

            assert pool.total_count == 2

            await pool.shutdown()

            assert pool.total_count == 0
            assert mock_destroy.call_count == 2


# ---------------------------------------------------------------------------
# TestGetSandboxPool (task 5.13)
# ---------------------------------------------------------------------------


class TestGetSandboxPool:
    async def test_pool_disabled_by_default(self) -> None:
        """Task 5.13: Pool disabled by default — no SandboxPool instance created."""
        from hecate.services.sandbox import _reset_pool_for_testing, get_sandbox_pool

        _reset_pool_for_testing()

        with patch("hecate.core.config.settings") as mock_settings:
            mock_settings.SANDBOX_POOL_ENABLED = False
            mock_settings.SANDBOX_POOL_SIZE = 3
            mock_settings.SANDBOX_MAX_USES = 50
            mock_settings.SANDBOX_POOL_BUSY_TTL = 1800
            mock_settings.SANDBOX_POOL_IDLE_TIMEOUT = 300
            mock_settings.SANDBOX_POOL_ACQUIRE_TIMEOUT = 30
            mock_settings.SANDBOX_POOL_EXHAUSTION_STRATEGY = "wait"

            pool = get_sandbox_pool()

        assert pool is None

        _reset_pool_for_testing()
