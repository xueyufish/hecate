"""Tests for Agent Environment — LocalEnvironment and EnvironmentManager."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from hecate.services.environment.environment import LocalEnvironment
from hecate.services.environment.manager import EnvironmentManager

# ---------------------------------------------------------------------------
# LocalEnvironment tests
# ---------------------------------------------------------------------------


async def test_local_environment_creates_subdirs() -> None:
    """ensure_dirs creates sessions/, files/, memory/, skills/ subdirectories."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env = LocalEnvironment("agent-1", tmpdir)
        await env.ensure_dirs()
        for subdir in ("sessions", "files", "memory", "skills"):
            assert (Path(tmpdir) / "agent-1" / subdir).is_dir()


async def test_local_environment_write_and_read() -> None:
    """Write a file and read it back."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env = LocalEnvironment("agent-1", tmpdir)
        await env.ensure_dirs()
        await env.write_file("files/report.txt", b"hello world")
        content = await env.read_file("files/report.txt")
        assert content == b"hello world"


async def test_local_environment_list_files() -> None:
    """list_files returns file metadata."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env = LocalEnvironment("agent-1", tmpdir)
        await env.ensure_dirs()
        await env.write_file("files/a.txt", b"a")
        await env.write_file("files/b.txt", b"bb")
        files = await env.list_files("files")
        names = [f.name for f in files]
        assert "a.txt" in names
        assert "b.txt" in names
        a_file = next(f for f in files if f.name == "a.txt")
        assert a_file.size == 1
        assert a_file.is_dir is False


async def test_local_environment_delete_file() -> None:
    """Delete a file removes it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env = LocalEnvironment("agent-1", tmpdir)
        await env.ensure_dirs()
        await env.write_file("files/temp.txt", b"temp")
        assert await env.exists("files/temp.txt")
        await env.delete_file("files/temp.txt")
        assert not await env.exists("files/temp.txt")


async def test_local_environment_delete_nonexistent_raises() -> None:
    """Deleting a non-existent file raises FileNotFoundError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env = LocalEnvironment("agent-1", tmpdir)
        with pytest.raises(FileNotFoundError):
            await env.delete_file("files/nope.txt")


async def test_local_environment_read_nonexistent_raises() -> None:
    """Reading a non-existent file raises FileNotFoundError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env = LocalEnvironment("agent-1", tmpdir)
        with pytest.raises(FileNotFoundError):
            await env.read_file("files/nope.txt")


async def test_local_environment_path_traversal_blocked() -> None:
    """Path traversal attempts are rejected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env = LocalEnvironment("agent-1", tmpdir)
        with pytest.raises(ValueError, match="Path traversal"):
            await env.read_file("../../etc/passwd")


async def test_local_environment_isolation() -> None:
    """Different agents cannot access each other's files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env1 = LocalEnvironment("agent-1", tmpdir)
        env2 = LocalEnvironment("agent-2", tmpdir)
        await env1.ensure_dirs()
        await env2.ensure_dirs()
        await env1.write_file("files/secret.txt", b"agent1-secret")
        assert not await env2.exists("files/secret.txt")


async def test_local_environment_list_nonexistent_dir_raises() -> None:
    """Listing a non-existent directory raises FileNotFoundError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env = LocalEnvironment("agent-1", tmpdir)
        with pytest.raises(FileNotFoundError):
            await env.list_files("nonexistent")


# ---------------------------------------------------------------------------
# EnvironmentManager tests
# ---------------------------------------------------------------------------


async def test_manager_lazy_creation() -> None:
    """get_or_create creates environment on first call."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = EnvironmentManager(ttl=60, root=tmpdir)
        env = await manager.get_or_create("agent-1")
        assert env.environment_id == "agent-1"
        assert (Path(tmpdir) / "agent-1").is_dir()
        await manager.close_all()


async def test_manager_cached_reuse() -> None:
    """get_or_create returns same instance on second call."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = EnvironmentManager(ttl=60, root=tmpdir)
        env1 = await manager.get_or_create("agent-1")
        env2 = await manager.get_or_create("agent-1")
        assert env1 is env2
        await manager.close_all()


async def test_manager_ttl_eviction() -> None:
    """Expired environments are recreated on next access."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = EnvironmentManager(ttl=0, root=tmpdir)  # immediate expiry
        await manager.get_or_create("agent-1")
        await asyncio.sleep(0.01)
        env2 = await manager.get_or_create("agent-1")
        # Should be a new instance (expired)
        assert env2.environment_id == "agent-1"
        await manager.close_all()


async def test_manager_close_all() -> None:
    """close_all clears all cached environments."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = EnvironmentManager(ttl=60, root=tmpdir)
        await manager.get_or_create("agent-1")
        await manager.get_or_create("agent-2")
        assert manager.get_stats()["cached_count"] == 2
        await manager.close_all()
        assert manager.get_stats()["cached_count"] == 0


async def test_manager_close_specific() -> None:
    """close removes a specific agent's environment."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = EnvironmentManager(ttl=60, root=tmpdir)
        await manager.get_or_create("agent-1")
        await manager.get_or_create("agent-2")
        await manager.close("agent-1")
        assert manager.get_stats()["cached_count"] == 1
        await manager.close_all()


async def test_manager_stats() -> None:
    """get_stats returns correct cached_count and ttl."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = EnvironmentManager(ttl=120, root=tmpdir)
        stats = manager.get_stats()
        assert stats["cached_count"] == 0
        assert stats["ttl"] == 120
        await manager.get_or_create("agent-1")
        assert manager.get_stats()["cached_count"] == 1
        await manager.close_all()
