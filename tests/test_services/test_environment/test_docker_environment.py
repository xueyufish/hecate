"""Tests for DockerEnvironment and EnvironmentManager backend selection.

Docker tests are skipped if no Docker daemon is available.
"""

from __future__ import annotations

import shutil
import tempfile
from unittest.mock import patch

import pytest

from hecate.services.environment.environment import LocalEnvironment
from hecate.services.environment.manager import EnvironmentManager


def _check_docker_available() -> bool:
    """Check if aiodocker and Docker daemon are available."""
    try:
        import aiodocker  # noqa: F401
    except ImportError:
        return False
    if not shutil.which("docker"):
        return False
    import subprocess

    try:
        result = subprocess.run(  # noqa: S603
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


_DOCKER_AVAILABLE = _check_docker_available()

docker_skip = pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="aiodocker or Docker daemon not available")


# ---------------------------------------------------------------------------
# EnvironmentManager backend selection tests
# ---------------------------------------------------------------------------


async def test_manager_default_backend_is_local() -> None:
    """Default AGENT_ENV_BACKEND creates LocalEnvironment."""
    with tempfile.TemporaryDirectory() as tmpdir, patch("hecate.core.config.settings.AGENT_ENV_BACKEND", "local"):
        manager = EnvironmentManager(root=tmpdir)
        env = await manager.get_or_create("agent-1")
        assert isinstance(env, LocalEnvironment)
        await manager.close_all()


async def test_manager_docker_backend_selection() -> None:
    """AGENT_ENV_BACKEND=docker creates DockerEnvironment."""
    from hecate.services.environment.docker_environment import DockerEnvironment

    with patch("hecate.core.config.settings.AGENT_ENV_BACKEND", "docker"):
        manager = EnvironmentManager()
        env = manager._create_environment("agent-1")
        assert isinstance(env, DockerEnvironment)


async def test_manager_invalid_backend_raises() -> None:
    """Invalid AGENT_ENV_BACKEND raises ValueError at init."""
    with pytest.raises(ValueError, match="Invalid AGENT_ENV_BACKEND"):
        EnvironmentManager(backend="invalid")


async def test_manager_local_backend_no_regressions() -> None:
    """Local backend preserves existing behavior (write/read/exists/delete)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = EnvironmentManager(root=tmpdir, backend="local")
        env = await manager.get_or_create("agent-regression")
        await env.write_file("files/test.txt", b"hello world")
        data = await env.read_file("files/test.txt")
        assert data == b"hello world"
        assert await env.exists("files/test.txt")
        await env.delete_file("files/test.txt")
        assert not await env.exists("files/test.txt")
        await manager.close_all()


async def test_manager_stats_includes_warm_pool() -> None:
    """get_stats returns warm_pool_count."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = EnvironmentManager(root=tmpdir)
        stats = manager.get_stats()
        assert "cached_count" in stats
        assert "warm_pool_count" in stats
        assert stats["warm_pool_count"] == 0


# ---------------------------------------------------------------------------
# DockerEnvironment tests (require Docker daemon)
# ---------------------------------------------------------------------------


@docker_skip
async def test_docker_environment_creates_container() -> None:
    """DockerEnvironment creates a container with subdirectories."""
    from hecate.services.environment.docker_environment import DockerEnvironment

    env = DockerEnvironment("test-agent-create")
    try:
        await env.ensure_dirs()
        result = await env.exec_shell(["ls", "/env"])
        assert result.exit_code == 0
        output = result.stdout.decode()
        for subdir in ("sessions", "files", "memory", "skills"):
            assert subdir in output
    finally:
        await env.remove()


@docker_skip
async def test_docker_environment_write_and_read() -> None:
    """DockerEnvironment write_file/read_file roundtrip."""
    from hecate.services.environment.docker_environment import DockerEnvironment

    env = DockerEnvironment("test-agent-io")
    try:
        await env.ensure_dirs()
        await env.write_file("files/report.txt", b"hello docker")
        data = await env.read_file("files/report.txt")
        assert data == b"hello docker"
    finally:
        await env.remove()


@docker_skip
async def test_docker_environment_exec_shell() -> None:
    """DockerEnvironment exec_shell runs inside container."""
    from hecate.services.environment.docker_environment import DockerEnvironment

    env = DockerEnvironment("test-agent-exec")
    try:
        await env.ensure_dirs()
        result = await env.exec_shell(["echo", "hello from container"])
        assert result.exit_code == 0
        assert b"hello from container" in result.stdout
    finally:
        await env.remove()


@docker_skip
async def test_docker_environment_exists_and_delete() -> None:
    """DockerEnvironment exists/delete work inside container."""
    from hecate.services.environment.docker_environment import DockerEnvironment

    env = DockerEnvironment("test-agent-exists")
    try:
        await env.ensure_dirs()
        await env.write_file("files/temp.txt", b"data")
        assert await env.exists("files/temp.txt")
        await env.delete_file("files/temp.txt")
        assert not await env.exists("files/temp.txt")
    finally:
        await env.remove()


@docker_skip
async def test_docker_environment_list_files() -> None:
    """DockerEnvironment list_files returns entries."""
    from hecate.services.environment.docker_environment import DockerEnvironment

    env = DockerEnvironment("test-agent-list")
    try:
        await env.ensure_dirs()
        await env.write_file("files/a.txt", b"a")
        await env.write_file("files/b.txt", b"b")
        files = await env.list_files("files")
        names = {f.name for f in files}
        assert "a.txt" in names
        assert "b.txt" in names
    finally:
        await env.remove()
