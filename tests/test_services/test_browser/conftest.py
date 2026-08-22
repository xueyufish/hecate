"""Pytest fixtures for browser subsystem tests.

Provides:
- ``mock_container``: a fake :class:`PooledContainer` with a deterministic id.
- ``mock_pool``: a fake :class:`SandboxPool` whose ``allocate`` returns a
  container and whose ``recycle`` records the recycle event.
- ``mock_subprocess``: monkeypatches ``asyncio.create_subprocess_exec`` to
  return scripted (returncode, stdout, stderr) tuples so driver HTTP calls
  can be exercised without a real Docker daemon.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from hecate.services.sandbox.pool import PooledContainer, SandboxPool


@pytest.fixture
def mock_container() -> PooledContainer:
    return PooledContainer(container_id="abcdef0123456789", use_count=1, in_use=True)


@pytest.fixture
def mock_pool(mock_container: PooledContainer) -> MagicMock:
    pool = MagicMock(spec=SandboxPool)
    pool.allocate = AsyncMock(return_value=mock_container)
    pool.recycle = AsyncMock(return_value=None)
    return pool


class _ScriptedProcess:
    """Minimal async-compatible stand-in for ``asyncio.subprocess.Process``."""

    def __init__(self, returncode: int, stdout: bytes, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


@pytest.fixture
def mock_subprocess(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, bytes]]:
    """Patch ``asyncio.create_subprocess_exec`` with a scriptable runner.

    Returns a list that tests can append ``(name_filter, response_bytes)``
    tuples to. Each ``docker exec ... curl ...`` call matches the most
    recently appended entry whose ``name_filter`` is a substring of the
    command (or the last entry if none matches).
    """
    script: list[tuple[str, bytes]] = []

    async def _fake_exec(*args: Any, **kwargs: Any) -> _ScriptedProcess:
        cmd_str = " ".join(str(a) for a in args)
        response = b""
        for name_filter, resp in reversed(script):
            if name_filter in cmd_str:
                response = resp
                break
        else:
            if script:
                response = script[-1][1]
        return _ScriptedProcess(returncode=0, stdout=response, stderr=b"")

    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec)
    return script


@pytest.fixture
def mock_subprocess_sequence(monkeypatch: pytest.MonkeyPatch) -> list[Iterable[tuple[int, bytes, bytes]]]:
    """Patch ``asyncio.create_subprocess_exec`` with a sequence of responses.

    Each element of the returned list is consumed in order; each element is
    an iterable of ``(returncode, stdout, stderr)`` tuples. Multiple
    subprocess calls (e.g. health checks) consume successive entries.
    """
    responses: list[Iterable[tuple[int, bytes, bytes]]] = []

    iterator_state: dict[str, Any] = {"seq": iter([])}

    async def _fake_exec(*_args: Any, **_kwargs: Any) -> _ScriptedProcess:
        if not responses:
            return _ScriptedProcess(returncode=0, stdout=b"", stderr=b"")
        try:
            entry = next(iterator_state["seq"])
        except StopIteration:
            entry = (0, b"", b"")
        rc, out, err = entry
        return _ScriptedProcess(returncode=rc, stdout=out, stderr=err)

    def _reset() -> None:
        if responses:
            iterator_state["seq"] = iter(responses[0])

    _reset()

    def _set_sequence(seq: Iterable[tuple[int, bytes, bytes]]) -> None:
        responses.clear()
        responses.append(seq)
        _reset()

    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec)
    # Attach a helper to the fixture for test convenience
    fixture = _set_sequence  # type: ignore[assignment]
    return fixture  # type: ignore[return-value]
