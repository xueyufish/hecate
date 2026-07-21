"""Tests for exec_shell on LocalEnvironment and ExecResult dataclass."""

from __future__ import annotations

import sys
import tempfile

import pytest

from hecate.services.environment.environment import ExecResult, LocalEnvironment


def test_exec_result_dataclass_fields() -> None:
    """ExecResult has exit_code, stdout, stderr fields with correct types."""
    result = ExecResult(exit_code=0, stdout=b"hello", stderr=b"")
    assert result.exit_code == 0
    assert result.stdout == b"hello"
    assert result.stderr == b""


def test_exec_result_negative_exit_code() -> None:
    """ExecResult accepts negative exit codes (timeout/error indicator)."""
    result = ExecResult(exit_code=-1, stdout=b"", stderr=b"timed out")
    assert result.exit_code == -1


async def test_local_exec_shell_basic() -> None:
    """exec_shell runs a command on the host and returns output."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env = LocalEnvironment("test-agent", tmpdir)
        await env.ensure_dirs()
        result = await env.exec_shell(["echo", "hello"])
        assert result.exit_code == 0
        assert result.stdout.strip() == b"hello"
        assert result.stderr == b""


async def test_local_exec_shell_with_cwd() -> None:
    """exec_shell respects the cwd parameter."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env = LocalEnvironment("test-agent", tmpdir)
        await env.ensure_dirs()
        result = await env.exec_shell(["pwd"], cwd="files")
        assert result.exit_code == 0
        assert b"files" in result.stdout


async def test_local_exec_shell_timeout() -> None:
    """exec_shell terminates the command after the timeout."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env = LocalEnvironment("test-agent", tmpdir)
        await env.ensure_dirs()
        result = await env.exec_shell(["sleep", "10"], timeout=0.5)
        assert result.exit_code == -1
        assert b"timed out" in result.stderr


async def test_local_exec_shell_captures_stderr() -> None:
    """exec_shell captures stderr separately from stdout."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env = LocalEnvironment("test-agent", tmpdir)
        await env.ensure_dirs()
        result = await env.exec_shell(["sh", "-c", "echo out; echo err >&2"])
        assert result.exit_code == 0
        assert b"out" in result.stdout
        assert b"err" in result.stderr
        assert b"err" not in result.stdout


async def test_local_exec_shell_nonexistent_command() -> None:
    """exec_shell returns error for nonexistent command."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env = LocalEnvironment("test-agent", tmpdir)
        await env.ensure_dirs()
        result = await env.exec_shell(["nonexistent-command-xyz"])
        assert result.exit_code == -1
        assert len(result.stderr) > 0


async def test_local_exec_shell_empty_command() -> None:
    """exec_shell returns error for empty command list."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env = LocalEnvironment("test-agent", tmpdir)
        await env.ensure_dirs()
        result = await env.exec_shell([])
        assert result.exit_code == -1


@pytest.mark.skipif(sys.platform == "win32", reason="Unix-only test")
async def test_local_exec_shell_exit_code() -> None:
    """exec_shell returns the correct exit code."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env = LocalEnvironment("test-agent", tmpdir)
        await env.ensure_dirs()
        result = await env.exec_shell(["sh", "-c", "exit 42"])
        assert result.exit_code == 42
