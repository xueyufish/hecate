"""Unit tests for SandboxPool."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from hecate.services.sandbox.executor import SandboxConfig, SandboxResult
from hecate.services.sandbox.pool import PooledContainer, SandboxPool


class TestPooledContainer:
    def test_init_defaults(self) -> None:
        c = PooledContainer(container_id="abc123")

        assert c.container_id == "abc123"
        assert c.use_count == 0
        assert c.in_use is False


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

    async def test_allocate_existing(self) -> None:
        pool = SandboxPool(pool_size=2)

        with patch.object(pool, "_create_fresh_container", new_callable=AsyncMock) as mock_create:
            mock_create.side_effect = ["c1", "c2"]
            await pool.prewarm()

            container = await pool.allocate()

            assert container.in_use is True
            assert container.use_count == 1
            assert pool.available_count == 1

    async def test_allocate_creates_new_when_exhausted(self) -> None:
        pool = SandboxPool(pool_size=1)

        with patch.object(pool, "_create_fresh_container", new_callable=AsyncMock) as mock_create:
            mock_create.side_effect = ["c1", "c2"]
            await pool.prewarm()
            await pool.allocate()

            assert pool.available_count == 0

            container = await pool.allocate()

            assert container.container_id == "c2"
            assert container.in_use is True
            assert pool.total_count == 2

    async def test_recycle_returns_to_pool(self) -> None:
        pool = SandboxPool(pool_size=1)

        with patch.object(pool, "_create_fresh_container", new_callable=AsyncMock) as mock_create, \
             patch.object(pool, "_clean_container", new_callable=AsyncMock):
            mock_create.return_value = "c1"
            await pool.prewarm()
            container = await pool.allocate()

            assert pool.available_count == 0

            await pool.recycle(container)

            assert container.in_use is False
            assert pool.available_count == 1

    async def test_recycle_destroys_at_max_uses(self) -> None:
        pool = SandboxPool(pool_size=1, max_uses=2)

        with patch.object(pool, "_create_fresh_container", new_callable=AsyncMock) as mock_create, \
             patch("hecate.services.sandbox.pool.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
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

    async def test_execute_delegates_to_executor(self) -> None:
        pool = SandboxPool(pool_size=1)
        pool._executor = AsyncMock()

        with patch.object(pool, "_create_fresh_container", new_callable=AsyncMock) as mock_create, \
             patch.object(pool, "_clean_container", new_callable=AsyncMock):
            mock_create.return_value = "c1"
            pool._executor.execute = AsyncMock(
                return_value=SandboxResult(exit_code=0, stdout="result", stderr="")
            )
            pool._executor.config = SandboxConfig()

            result = await pool.execute("tool", {"arg": "val"})

            assert result.exit_code == 0
            assert result.stdout == "result"

    async def test_shutdown(self) -> None:
        pool = SandboxPool(pool_size=2)

        with patch.object(pool, "_create_fresh_container", new_callable=AsyncMock) as mock_create, \
             patch.object(pool, "_destroy_container", new_callable=AsyncMock) as mock_destroy:
            mock_create.side_effect = ["c1", "c2"]
            await pool.prewarm()

            assert pool.total_count == 2

            await pool.shutdown()

            assert pool.total_count == 0
            assert mock_destroy.call_count == 2
