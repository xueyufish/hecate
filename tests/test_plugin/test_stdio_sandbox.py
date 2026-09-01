"""Tests for sandboxed stdio MCP execution (5.5c task group 6)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from hecate.core.plugin.agent_plugins import McpServerSpec
from hecate.core.plugin.stdio_sandbox import (
    CONTAINER_PLUGIN_DATA,
    CONTAINER_PLUGIN_ROOT,
    StdioSandboxError,
    build_sandbox_command,
)

ALLOWLIST = ["npx", "uvx"]


def _entry(**overrides: object) -> dict:
    entry: dict = {
        "name": "local",
        "transport": "stdio",
        "endpoint": "npx",
        "args": ["-y", "some-mcp-server"],
        "env": {},
        "cwd": "${PLUGIN_ROOT}",
    }
    entry.update(overrides)
    return entry


class TestBuildSandboxCommand:
    """Wrapper generation with mounts and placeholder translation."""

    def test_wrapper_shape(self, tmp_path: Path) -> None:
        command, argv = build_sandbox_command("pkg", _entry(), tmp_path, "hecate-plugin-runner:latest", ALLOWLIST)
        assert command == "docker"
        joined = " ".join(argv)
        assert argv[0] == "run" and "--rm" in argv and "--interactive" in argv
        assert f":{CONTAINER_PLUGIN_ROOT}:ro" in joined
        assert f":{CONTAINER_PLUGIN_DATA}" in joined
        assert argv[-2:] == ["npx", "-y"] or "npx" in argv
        assert "some-mcp-server" in argv
        # per-plugin data dir materialized
        assert (tmp_path / "agent-plugins" / ".data" / "pkg").is_dir()

    def test_cwd_placeholder_translation(self, tmp_path: Path) -> None:
        _, argv = build_sandbox_command("pkg", _entry(cwd="${PLUGIN_DATA}/cache"), tmp_path, "img", ALLOWLIST)
        idx = argv.index("--workdir")
        assert argv[idx + 1] == f"{CONTAINER_PLUGIN_DATA}/cache"

    def test_relative_cwd_translation(self, tmp_path: Path) -> None:
        _, argv = build_sandbox_command("pkg", _entry(cwd="./sub"), tmp_path, "img", ALLOWLIST)
        idx = argv.index("--workdir")
        assert argv[idx + 1] == f"{CONTAINER_PLUGIN_ROOT}/sub"

    def test_env_passed_as_flags(self, tmp_path: Path) -> None:
        _, argv = build_sandbox_command("pkg", _entry(env={"MODE": "fast"}), tmp_path, "img", ALLOWLIST)
        assert "--env" in argv and "MODE=fast" in argv

    def test_command_outside_allowlist_denied(self, tmp_path: Path) -> None:
        with pytest.raises(StdioSandboxError, match="not in allowlist"):
            build_sandbox_command("pkg", _entry(endpoint="bash"), tmp_path, "img", ALLOWLIST)

    def test_empty_allowlist_fail_closed(self, tmp_path: Path) -> None:
        with pytest.raises(StdioSandboxError, match="fail-closed"):
            build_sandbox_command("pkg", _entry(), tmp_path, "img", [])

    def test_missing_runner_image_fail_closed(self, tmp_path: Path) -> None:
        with pytest.raises(StdioSandboxError, match="runner image"):
            build_sandbox_command("pkg", _entry(), tmp_path, "", ALLOWLIST)

    def test_dangerous_args_denied(self, tmp_path: Path) -> None:
        with pytest.raises(StdioSandboxError, match="arbitrary code"):
            build_sandbox_command("pkg", _entry(args=["-y", "s", "-c", "rm -rf /"]), tmp_path, "img", ALLOWLIST)

    def test_spec_object_accepted(self, tmp_path: Path) -> None:
        spec = McpServerSpec(
            server_name="local",
            transport="stdio",
            endpoint="uvx",
            args=["mcp-server"],
        )
        command, argv = build_sandbox_command("pkg", spec, tmp_path, "img", ALLOWLIST)
        assert command == "docker"
        assert "uvx" in argv and "mcp-server" in argv


def _docker_available() -> bool:
    """Docker counts as available only when the daemon answers.

    CLI presence alone is not enough: a machine with docker installed
    but the daemon stopped fails ``docker run`` with exit 125 ("Cannot
    connect to the Docker daemon"), which surfaces as a test failure
    instead of the intended skip.
    """
    if shutil.which("docker") is None:
        return False
    try:
        proc = subprocess.run(  # noqa: S603
            ["docker", "info"],  # noqa: S607
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


@pytest.mark.skipif(not _docker_available(), reason="docker not available")
class TestStdioSandboxIntegration:
    """Live docker run (skipped when docker is absent)."""

    def test_container_echo_roundtrip(self, tmp_path: Path) -> None:
        """The wrapper form executes inside a container and streams stdio."""
        entry = _entry(endpoint="echo", args=["hello-from-container"])
        entry["endpoint"] = "echo"  # allowlist-bypassed below by allowlist=["echo"]
        command, argv = build_sandbox_command("pkg", entry, tmp_path, "alpine:3.20", ["echo"])
        proc = subprocess.run(  # noqa: S603
            [command, *argv], capture_output=True, text=True, timeout=120
        )
        assert proc.returncode == 0
        assert "hello-from-container" in proc.stdout
