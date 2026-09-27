"""RuntimePort LLM contract — production adapter vs contract fake.

Proves that the engine's LLM consumers (LLMWorker, the direct loop's
successors) see the same stream shape from any RuntimePort: content
chunks, terminal structured tool_calls, usage pass-through. New
RuntimePort implementations must satisfy this contract and register
themselves in ``PORT_FACTORIES``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any

from hecate.core.composition.agent_execution_port import AgentExecutionPort  # noqa: F401 — consumer smoke
from hecate.core.composition.runtime_port_adapter import _ProductionRuntimePort


class FakeLLMPort:
    """Contract fake replaying scripted turns with the same stream shape
    as the production adapter (content deltas, terminal tool_calls,
    terminal usage chunk)."""

    def __init__(self, turns: list[dict[str, Any]]) -> None:
        self.turns = turns

    async def llm_invoke_structured(self, messages, config) -> AsyncGenerator[dict[str, Any], None]:
        for turn in self.turns:
            if turn.get("content"):
                yield {"content": turn["content"], "tool_calls": None}
            if turn.get("tool_calls"):
                yield {"content": None, "tool_calls": turn["tool_calls"]}
            if turn.get("usage"):
                yield {"content": None, "tool_calls": None, "usage": turn["usage"]}


class _StreamingBase:
    """Minimal port surface for streaming-only exercises."""

    def __init__(self, turns: list[dict[str, Any]]) -> None:
        self._turns = turns

    async def llm_invoke_structured(self, messages, config) -> AsyncGenerator[dict[str, Any], None]:
        async for chunk in FakeLLMPort(self._turns).llm_invoke_structured(messages, config):
            yield chunk

    async def llm_invoke(self, messages, config) -> AsyncGenerator[str, None]:
        async for chunk in self.llm_invoke_structured(messages, config):
            if chunk.get("content"):
                yield chunk["content"]


def _production_port(turns: list[dict[str, Any]]) -> _ProductionRuntimePort:
    """Production adapter with a scripted LiteLLM-shaped backend."""

    class _Backend:
        async def chat_stream(self, messages, model, tools, **kwargs):
            for turn in turns:
                if turn.get("content"):
                    yield {"content": turn["content"], "tool_calls": None}
                if turn.get("tool_calls"):
                    for i, tc in enumerate(turn["tool_calls"]):
                        # LiteLLM delivers attribute-style delta objects —
                        # the production adapter reads them via getattr.
                        yield {
                            "content": None,
                            "tool_calls": [
                                SimpleNamespace(
                                    index=i,
                                    id=tc["id"],
                                    type="function",
                                    function=SimpleNamespace(
                                        name=tc["function"]["name"],
                                        arguments=tc["function"]["arguments"],
                                    ),
                                )
                            ],
                        }
                if turn.get("usage"):
                    yield {"content": None, "tool_calls": None, "usage": turn["usage"]}

    return _ProductionRuntimePort(db=None, llm_service=_Backend())  # type: ignore[arg-type]


PORT_FACTORIES: dict[str, Any] = {
    "production": lambda: _production_port(_TURNS),
    "fake": lambda: _StreamingBase(_TURNS),
}

_TURNS: list[dict[str, Any]] = [
    {"content": "thinking", "tool_calls": None},
    {
        "content": None,
        "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "search", "arguments": '{"q": "x"}'}}],
    },
    {"content": None, "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
]


def _collect_chunks(port: Any) -> dict[str, Any]:
    import asyncio

    async def run() -> dict[str, Any]:
        text_parts: list[str] = []
        tool_calls: list[Any] = []
        usage: dict[str, Any] | None = None
        async for chunk in port.llm_invoke_structured([{"role": "user", "content": "go"}], {"model": "m"}):
            if chunk.get("content"):
                text_parts.append(chunk["content"])
            if chunk.get("tool_calls"):
                tool_calls.append(chunk["tool_calls"])
            if chunk.get("usage"):
                usage = chunk["usage"]
        return {"text": "".join(text_parts), "tool_call_batches": tool_calls, "usage": usage}

    return asyncio.run(run())


def test_contract_llm_stream_shape_identical() -> None:
    prod = _collect_chunks(PORT_FACTORIES["production"]())
    fake = _collect_chunks(PORT_FACTORIES["fake"]())

    # Same text, same single terminal tool_call batch (assembled per index),
    # same usage pass-through.
    assert prod["text"] == fake["text"] == "thinking"
    assert len(prod["tool_call_batches"]) == len(fake["tool_call_batches"]) == 1
    assert (
        prod["usage"]
        == fake["usage"]
        == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }
    )


def test_contract_production_tool_call_assembly_shape() -> None:
    """The production adapter assembles streamed tool_call deltas into one
    complete call — the shape Workers consume."""
    port = PORT_FACTORIES["production"]()
    import asyncio

    async def run() -> list[Any]:
        batches = []
        async for chunk in port.llm_invoke_structured([{"role": "user", "content": "go"}], {"model": "m"}):
            if chunk.get("tool_calls"):
                batches.append(chunk["tool_calls"])
        return batches

    batches = asyncio.run(run())
    assert len(batches) == 1
    call = batches[0][0]
    assert call["id"] == "call-1"
    assert call["function"]["name"] == "search"
