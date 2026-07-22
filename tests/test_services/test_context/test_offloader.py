"""Tests for ContextOffloader.

Tests cover:
- Offload writes valid JSON to the environment with full message structure
- Stub format: role=system, content ≤ 500 chars, includes file path and read_file hint
- Heuristic topic summary extraction from user messages
- Filename timestamp format and same-second collision handling
- is_enabled() returns False when no environment is attached
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from hecate.services.context.offloader import ContextOffloader
from hecate.services.environment.environment import LocalEnvironment


@pytest.fixture
def env(tmp_path: Any) -> LocalEnvironment:
    return LocalEnvironment(agent_id="test-agent", root=str(tmp_path))


class TestContextOffloaderEnabled:
    def test_disabled_without_environment(self) -> None:
        offloader = ContextOffloader()
        assert not offloader.is_enabled()

    def test_enabled_with_environment(self, env: LocalEnvironment) -> None:
        offloader = ContextOffloader(environment=env)
        assert offloader.is_enabled()

    def test_threshold_default(self) -> None:
        offloader = ContextOffloader()
        assert offloader.threshold_tokens == 6000

    def test_threshold_custom(self) -> None:
        offloader = ContextOffloader(threshold_tokens=100)
        assert offloader.threshold_tokens == 100


class TestOffload:
    async def test_offload_writes_valid_json(self, env: LocalEnvironment) -> None:
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        offloader = ContextOffloader(environment=env)
        await offloader.offload(messages, "session-1")

        files = await env.list_files("memory/sessions/session-1")
        assert len(files) == 1
        raw = await env.read_file(files[0].path)
        parsed = json.loads(raw)
        assert isinstance(parsed, list)
        assert len(parsed) == 2
        assert parsed[0] == messages[0]
        assert parsed[1] == messages[1]

    async def test_offload_preserves_tool_calls(self, env: LocalEnvironment) -> None:
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "t1", "function": {"name": "search", "arguments": "{}"}}],
            },
            {"role": "tool", "content": "result", "tool_call_id": "t1"},
        ]
        offloader = ContextOffloader(environment=env)
        await offloader.offload(messages, "s1")

        files = await env.list_files("memory/sessions/s1")
        raw = await env.read_file(files[0].path)
        parsed = json.loads(raw)
        assert parsed[0]["tool_calls"] == messages[0]["tool_calls"]
        assert parsed[1]["tool_call_id"] == "t1"


class TestStubFormat:
    async def test_stub_role_is_system(self, env: LocalEnvironment) -> None:
        offloader = ContextOffloader(environment=env)
        stub = await offloader.offload([{"role": "user", "content": "hi"}], "s1")
        assert stub["role"] == "system"

    async def test_stub_content_under_500_chars(self, env: LocalEnvironment) -> None:
        big_messages = [{"role": "user", "content": "x" * 5000} for _ in range(20)]
        offloader = ContextOffloader(environment=env)
        stub = await offloader.offload(big_messages, "s1")
        assert len(stub["content"]) <= 500

    async def test_stub_includes_file_path(self, env: LocalEnvironment) -> None:
        offloader = ContextOffloader(environment=env)
        stub = await offloader.offload([{"role": "user", "content": "hi"}], "s1")
        assert "memory/sessions/s1/offloaded_" in stub["content"]

    async def test_stub_includes_read_file_hint(self, env: LocalEnvironment) -> None:
        offloader = ContextOffloader(environment=env)
        stub = await offloader.offload([{"role": "user", "content": "hi"}], "s1")
        assert 'read_file("' in stub["content"]


class TestHeuristicSummary:
    def test_extracts_user_message_prefixes(self) -> None:
        messages = [
            {"role": "user", "content": "Tell me about Python"},
            {"role": "assistant", "content": "Python is great"},
            {"role": "user", "content": "What about async?"},
        ]
        summary = ContextOffloader._heuristic_summary(messages)
        assert "Tell me about Python" in summary
        assert "What about async?" in summary

    def test_ignores_non_user_messages(self) -> None:
        messages = [{"role": "assistant", "content": "system stuff"}]
        summary = ContextOffloader._heuristic_summary(messages)
        assert summary == "no user messages"

    def test_truncates_long_summary(self) -> None:
        messages = [{"role": "user", "content": "w" * 5000} for _ in range(10)]
        summary = ContextOffloader._heuristic_summary(messages)
        assert len(summary) <= 500 - 120 + 3

    def test_empty_messages(self) -> None:
        summary = ContextOffloader._heuristic_summary([])
        assert summary == "no user messages"


class TestFilenameTimestamp:
    def test_timestamp_format(self) -> None:
        ts = ContextOffloader._timestamp()
        assert len(ts) == 14
        assert ts.isdigit()

    async def test_same_second_collision_suffix(self, env: LocalEnvironment) -> None:
        offloader = ContextOffloader(environment=env)
        await offloader.offload([{"role": "user", "content": "first"}], "s1")
        # Second offload in the same test run will likely collide on timestamp
        await offloader.offload([{"role": "user", "content": "second"}], "s1")
        files = await env.list_files("memory/sessions/s1")
        assert len(files) == 2


class TestOffloadRetrieval:
    async def test_offloaded_content_retrievable_via_read_file(self, env: LocalEnvironment) -> None:
        messages = [
            {"role": "user", "content": "important question"},
            {"role": "assistant", "content": "important answer"},
        ]
        offloader = ContextOffloader(environment=env)
        stub = await offloader.offload(messages, "s1")

        files = await env.list_files("memory/sessions/s1")
        path = files[0].path
        raw = await env.read_file(path)
        parsed = json.loads(raw)
        assert parsed[0]["content"] == "important question"
        assert path in stub["content"]
