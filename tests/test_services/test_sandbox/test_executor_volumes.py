"""Tests for SandboxExecutor volume mounting.

Covers SandboxConfig.volumes and _create_container() volume args.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from hecate.services.sandbox.executor import SandboxConfig, SandboxExecutor


class TestSandboxConfigVolumes:
    def test_default_volumes_empty(self) -> None:
        cfg = SandboxConfig()
        assert cfg.volumes == {}

    def test_volumes_set(self) -> None:
        cfg = SandboxConfig(volumes={"/data": "/mnt/env"})
        assert cfg.volumes == {"/data": "/mnt/env"}


class TestSandboxExecutorVolumeArgs:
    async def test_no_volumes_produces_no_mount_args(self) -> None:
        executor = SandboxExecutor(config=SandboxConfig())
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (b"container-123", b"")
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            await executor._create_container("test", {}, executor.config)
            call_args = mock_exec.call_args[0]
            assert "--volume" not in call_args

    async def test_volumes_produces_mount_args(self) -> None:
        cfg = SandboxConfig(volumes={"/host/data": "/mnt/env"})
        executor = SandboxExecutor(config=cfg)
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (b"container-123", b"")
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            await executor._create_container("test", {}, executor.config)
            call_args = mock_exec.call_args[0]
            assert "--volume" in call_args
            vol_idx = call_args.index("--volume")
            assert call_args[vol_idx + 1] == "/host/data:/mnt/env:rw"

    async def test_multiple_volumes(self) -> None:
        cfg = SandboxConfig(volumes={"/a": "/mnt/a", "/b": "/mnt/b"})
        executor = SandboxExecutor(config=cfg)
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (b"container-123", b"")
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            await executor._create_container("test", {}, executor.config)
            call_args = mock_exec.call_args[0]
            vol_indices = [i for i, arg in enumerate(call_args) if arg == "--volume"]
            assert len(vol_indices) == 2

    async def test_mount_mode_from_config(self) -> None:
        cfg = SandboxConfig(volumes={"/host": "/mnt/env"})
        executor = SandboxExecutor(config=cfg)
        with (
            patch("asyncio.create_subprocess_exec") as mock_exec,
            patch("hecate.core.config.settings.SANDBOX_MOUNT_MODE", "ro"),
        ):
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (b"container-123", b"")
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            await executor._create_container("test", {}, executor.config)
            call_args = mock_exec.call_args[0]
            vol_idx = call_args.index("--volume")
            assert call_args[vol_idx + 1] == "/host:/mnt/env:ro"
