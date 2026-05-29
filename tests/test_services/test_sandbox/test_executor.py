"""Unit tests for SandboxExecutor."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from hecate.services.sandbox.executor import (
    SandboxConfig,
    SandboxExecutor,
    SandboxResult,
)


class TestSandboxConfig:
    def test_defaults(self) -> None:
        cfg = SandboxConfig()

        assert cfg.image == "hecate-sandbox:latest"
        assert cfg.timeout_seconds == 30
        assert cfg.memory_limit == "128m"
        assert cfg.network_mode == "none"
        assert cfg.read_only_fs is True

    def test_custom_config(self) -> None:
        cfg = SandboxConfig(
            image="custom:1.0",
            timeout_seconds=60,
            memory_limit="256m",
            network_mode="bridge",
        )

        assert cfg.image == "custom:1.0"
        assert cfg.timeout_seconds == 60
        assert cfg.memory_limit == "256m"
        assert cfg.network_mode == "bridge"


class TestSandboxExecutor:
    async def test_execute_success(self) -> None:
        executor = SandboxExecutor()

        with (
            patch.object(executor, "_create_container", new_callable=AsyncMock) as mock_create,
            patch.object(executor, "_wait_container", new_callable=AsyncMock) as mock_wait,
            patch.object(executor, "_destroy_container", new_callable=AsyncMock),
        ):
            mock_create.return_value = "container-abc"
            mock_wait.return_value = SandboxResult(exit_code=0, stdout='{"result": 42}', stderr="")

            result = await executor.execute("calculator", {"expr": "6*7"})

            assert result.exit_code == 0
            assert "42" in result.stdout
            assert result.timed_out is False

    async def test_execute_timeout(self) -> None:
        executor = SandboxExecutor(config=SandboxConfig(timeout_seconds=5))

        with (
            patch.object(executor, "_create_container", new_callable=AsyncMock) as mock_create,
            patch.object(executor, "_destroy_container", new_callable=AsyncMock),
        ):
            mock_create.return_value = "container-timeout"

            with patch.object(executor, "_wait_container", new_callable=AsyncMock) as mock_wait:
                mock_wait.side_effect = TimeoutError("timed out")

                result = await executor.execute("slow_tool", {})

                assert result.timed_out is True
                assert result.exit_code == -1
                assert "timed out" in result.stderr

    async def test_execute_creation_failure(self) -> None:
        executor = SandboxExecutor()

        with patch.object(executor, "_create_container", new_callable=AsyncMock) as mock_create:
            mock_create.side_effect = RuntimeError("Docker not available")

            result = await executor.execute("tool", {})

            assert result.exit_code == -1
            assert "Docker not available" in result.stderr

    async def test_execute_with_custom_config(self) -> None:
        executor = SandboxExecutor()
        custom_cfg = SandboxConfig(timeout_seconds=120, memory_limit="512m")

        with (
            patch.object(executor, "_create_container", new_callable=AsyncMock) as mock_create,
            patch.object(executor, "_wait_container", new_callable=AsyncMock) as mock_wait,
            patch.object(executor, "_destroy_container", new_callable=AsyncMock),
        ):
            mock_create.return_value = "container-xyz"
            mock_wait.return_value = SandboxResult(exit_code=0, stdout="ok", stderr="")

            await executor.execute("tool", {}, config=custom_cfg)

            mock_create.assert_called_once_with("tool", {}, custom_cfg)
