"""Tests for the EnginePort abstract interface.

Covers the optional ``llm_invoke_structured`` method's default implementation,
which delegates to ``llm_invoke`` and yields a single chunk with
``tool_calls=None``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from hecate.engine.ports import EnginePort


class _MinimalPort(EnginePort):
    """Minimal concrete EnginePort — only implements the abstract methods.

    Used to verify that the optional ``llm_invoke_structured`` default
    implementation delegates to ``llm_invoke`` and produces a single chunk.
    """

    def __init__(self, tokens: list[str]) -> None:
        self._tokens = tokens

    async def llm_invoke(self, messages: list[dict], config: dict) -> AsyncGenerator[str, None]:
        for t in self._tokens:
            yield t

    async def tool_execute(self, name: str, args: dict, context: dict | None = None) -> Any:
        raise NotImplementedError

    async def knowledge_query(self, query: str, kb_ids: list) -> list[dict]:
        return []

    async def checkpoint_save(self, state: dict):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    async def checkpoint_load(self, checkpoint_id):  # type: ignore[no-untyped-def]
        return {}

    async def conversation_load(self, session_id):  # type: ignore[no-untyped-def]
        return []

    async def conversation_save(self, session_id, messages: list[dict]) -> None:
        return None

    async def create_span(self, name: str, parent_id: str | None = None, attributes: dict | None = None):
        return None

    async def end_span(self, span_id: str, output_data: dict | None = None, usage: dict | None = None) -> None:
        return None


class TestEnginePortLLMInvokeStructured:
    async def test_default_delegates_to_llm_invoke(self) -> None:
        port = _MinimalPort(["Hello", " ", "world"])
        chunks: list[dict[str, Any]] = []
        async for chunk in port.llm_invoke_structured(messages=[{"role": "user", "content": "Hi"}], config={}):
            chunks.append(chunk)
        assert len(chunks) == 1
        assert chunks[0]["content"] == "Hello world"
        assert chunks[0]["tool_calls"] is None

    async def test_default_with_empty_stream(self) -> None:
        port = _MinimalPort([])
        chunks: list[dict[str, Any]] = []
        async for chunk in port.llm_invoke_structured(messages=[], config={}):
            chunks.append(chunk)
        assert chunks == [{"content": "", "tool_calls": None}]

    async def test_default_passes_config_through(self) -> None:
        captured: list[dict] = []

        class _CapturingPort(_MinimalPort):
            async def llm_invoke(self, messages: list[dict], config: dict) -> AsyncGenerator[str, None]:
                captured.append({"messages": messages, "config": config})
                for t in self._tokens:
                    yield t

        port = _CapturingPort(["x"])
        async for _ in port.llm_invoke_structured(
            messages=[{"role": "user", "content": "Hi"}],
            config={"model": "gpt-4o", "tools": [{"type": "function"}]},
        ):
            pass
        assert captured == [
            {
                "messages": [{"role": "user", "content": "Hi"}],
                "config": {"model": "gpt-4o", "tools": [{"type": "function"}]},
            }
        ]
