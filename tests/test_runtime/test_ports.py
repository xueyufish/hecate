"""Tests for the RuntimePort abstract interface.

Covers the optional ``llm_invoke_structured`` method's default implementation,
which delegates to ``llm_invoke`` and yields a single chunk with
``tool_calls=None``.

Also verifies:
- ``RuntimePort`` cannot be instantiated directly (it is an ``abc.ABC``).
- ``StubRuntimePort`` (the canonical test double) is a usable subclass.
- ``tool_execute_sandbox`` default implementation delegates to ``tool_execute``
  without importing ``hecate_sandbox.sandbox`` (engine layering invariant).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

import pytest

from hecate.runtime.ports import RuntimePort, StubRuntimePort


class _MinimalPort(RuntimePort):
    """Minimal concrete RuntimePort — only implements the abstract methods.

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


class TestRuntimePortLLMInvokeStructured:
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


class TestRuntimePortABC:
    def test_runtime_port_cannot_be_instantiated(self) -> None:
        """RuntimePort is an abstract base class and must not be instantiable."""
        with pytest.raises(TypeError):
            RuntimePort()  # type: ignore[abstract,call-arg]

    def test_stub_runtime_port_can_be_instantiated(self) -> None:
        """StubRuntimePort implements all abstract methods and is instantiable."""
        port = StubRuntimePort()
        assert isinstance(port, RuntimePort)

    async def test_stub_runtime_port_llm_invoke_yields_token(self) -> None:
        port = StubRuntimePort()
        tokens: list[str] = []
        async for token in port.llm_invoke(messages=[], config={}):
            tokens.append(token)
        assert tokens == ["stub-response"]

    async def test_stub_runtime_port_tool_execute_returns_echo(self) -> None:
        port = StubRuntimePort()
        result = await port.tool_execute("my_tool", {"k": "v"})
        assert result == {"name": "my_tool", "args": {"k": "v"}, "context": None}

    async def test_stub_runtime_port_knowledge_query_returns_empty(self) -> None:
        port = StubRuntimePort()
        result = await port.knowledge_query("q", [UUID(int=1)])
        assert result == []

    async def test_stub_runtime_port_checkpoint_returns_uuid(self) -> None:
        port = StubRuntimePort()
        cid = await port.checkpoint_save({"x": 1})
        assert isinstance(cid, UUID)

    async def test_stub_runtime_port_create_span_returns_none(self) -> None:
        port = StubRuntimePort()
        assert await port.create_span("test-span") is None


class TestRuntimePortDefaultToolExecuteSandbox:
    async def test_default_falls_back_to_tool_execute(self) -> None:
        """The default ``tool_execute_sandbox`` must not import services.sandbox.

        This is the engine-layer layering invariant: ``engine/`` modules must
        not depend on ``hecate.services`` at import time. The default
        implementation here simply delegates to ``tool_execute``.
        """
        port = StubRuntimePort()
        result = await port.tool_execute_sandbox("my_tool", {"k": "v"})
        # StubRuntimePort.tool_execute echoes back the inputs.
        assert result == {"name": "my_tool", "args": {"k": "v"}, "context": None}

    async def test_default_does_not_import_sandbox_module(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ensure ``tool_execute_sandbox`` default does not pull in sandbox service.

        We monkeypatch the import to fail if the default implementation ever
        tries to import ``hecate_sandbox.sandbox``. Any concrete adapter that
        wishes to support sandbox execution MUST override this method.
        """

        def _explode(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "hecate_sandbox.sandbox":
                raise AssertionError(
                    "RuntimePort.tool_execute_sandbox default must not import "
                    "hecate_sandbox.sandbox — concrete adapters should override."
                )
            return _real_import(name, *args, **kwargs)

        import builtins

        _real_import = builtins.__import__
        monkeypatch.setattr(builtins, "__import__", _explode)

        port = StubRuntimePort()
        # If the default implementation tried to import hecate_sandbox.sandbox,
        # the monkeypatched import would raise AssertionError.
        await port.tool_execute_sandbox("t", {})
