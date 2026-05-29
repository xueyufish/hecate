"""Integration tests for sandbox execution with Docker.

These tests verify sandbox container lifecycle with a real Docker daemon.
They are skipped when Docker is not available.
"""

from __future__ import annotations

import subprocess

import pytest


def _docker_available() -> bool:
    try:
        result = subprocess.run(  # noqa: S607
            ["docker", "info"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker not available for integration tests",
)


class TestSandboxDockerIntegration:
    async def test_executor_create_and_run(self) -> None:
        from hecate.services.sandbox.executor import SandboxConfig, SandboxExecutor

        cfg = SandboxConfig(image="alpine:latest")
        executor = SandboxExecutor(config=cfg)

        result = await executor.execute("echo", {"message": "hello"})

        assert result is not None
        assert result.exit_code is not None
