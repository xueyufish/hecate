"""Tests for the 4.13 recall tool — reload offloaded blocks read-only."""

from __future__ import annotations

import json

import pytest

from hecate.tools.tool.builtin import BUILTIN_TOOL_DEFINITIONS, BuiltInToolExecutor
from hecate.tools.tool.search import SearchProvider


class MockSearchProvider(SearchProvider):
    async def search(self, query: str, max_results: int = 5) -> list[dict]:
        return []


class _StubEnvironment:
    """In-memory stand-in for AgentEnvironment (read_file/exists)."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self._files = files

    async def read_file(self, path: str) -> bytes:
        if path not in self._files:
            raise FileNotFoundError(f"File not found: {path}")
        return self._files[path]

    async def exists(self, path: str) -> bool:
        return path in self._files


_OFFLOAD_PATH = "memory/sessions/s1/offloaded_20260916.json"
_OFFLOAD_MESSAGES = [
    {"role": "user", "content": "earlier question"},
    {"role": "assistant", "content": "earlier answer"},
]


def _env_with_file() -> _StubEnvironment:
    return _StubEnvironment({_OFFLOAD_PATH: json.dumps(_OFFLOAD_MESSAGES).encode("utf-8")})


def _executor() -> BuiltInToolExecutor:
    return BuiltInToolExecutor(search_provider=MockSearchProvider(), workspace_root="./workspace")


def test_recall_definition_registered() -> None:
    assert "recall" in BUILTIN_TOOL_DEFINITIONS
    definition = BUILTIN_TOOL_DEFINITIONS["recall"]
    assert definition["risk_level"] == "LOW"
    assert definition["parameters"]["required"] == ["path"]


async def test_recall_returns_full_block() -> None:
    executor = _executor()
    result = await executor.execute(
        "recall",
        {"path": _OFFLOAD_PATH},
        {"environment": _env_with_file(), "session_id": "s1"},
    )
    parsed = json.loads(result)
    assert parsed["path"] == _OFFLOAD_PATH
    assert parsed["messages"] == _OFFLOAD_MESSAGES


async def test_recall_preserves_all_message_fields() -> None:
    env = _StubEnvironment(
        {
            _OFFLOAD_PATH: json.dumps(
                [{"role": "assistant", "tool_calls": [{"id": "c1"}], "tool_call_id": None, "content": "x"}]
            ).encode("utf-8")
        }
    )
    result = await _executor().execute(
        "recall",
        {"path": _OFFLOAD_PATH},
        {"environment": env, "session_id": "s1"},
    )
    parsed = json.loads(result)
    assert parsed["messages"][0]["tool_calls"] == [{"id": "c1"}]


async def test_recall_unknown_path_errors() -> None:
    with pytest.raises(FileNotFoundError, match="Offload file not found"):
        await _executor().execute(
            "recall",
            {"path": "memory/sessions/s1/missing.json"},
            {"environment": _StubEnvironment({}), "session_id": "s1"},
        )


async def test_recall_rejects_foreign_session_paths() -> None:
    with pytest.raises(ValueError, match="not part of this session"):
        await _executor().execute(
            "recall",
            {"path": _OFFLOAD_PATH},
            {"environment": _env_with_file(), "session_id": "other-session"},
        )


async def test_recall_requires_environment() -> None:
    with pytest.raises(ValueError, match="no agent environment"):
        await _executor().execute(
            "recall",
            {"path": "memory/sessions/s1/x.json"},
            {"session_id": "s1"},
        )


async def test_recall_requires_session_context() -> None:
    with pytest.raises(ValueError, match="session context"):
        await _executor().execute(
            "recall",
            {"path": _OFFLOAD_PATH},
            {"environment": _env_with_file()},
        )
