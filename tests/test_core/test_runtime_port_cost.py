"""Tests for RuntimePort LLM cost accounting (p1-audit-cost-a2a).

Covers the model-cost-management delta: parameter passthrough, provider
usage adoption, chunk-invariant estimation, and non-zero usage for
tool-only invocations.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from hecate.core.composition.runtime_port_adapter import (
    _ProductionRuntimePort,
    set_quota_service_factory,
)


class _FakeQuotaService:
    def __init__(self) -> None:
        self.recorded: list[dict[str, Any]] = []

    async def record_usage(self, **kwargs: Any) -> None:
        self.recorded.append(kwargs)


class _FakeLLM:
    """chat_stream stub yielding scripted chunks."""

    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self.chunks = chunks
        self.calls: list[dict[str, Any]] = []

    async def chat_stream(self, **kwargs: Any) -> AsyncGenerator[dict[str, Any], None]:
        self.calls.append(kwargs)
        for chunk in self.chunks:
            yield chunk


def _make_port(llm: _FakeLLM, quota: _FakeQuotaService) -> _ProductionRuntimePort:
    set_quota_service_factory(lambda db, workspace_id: quota)
    return _ProductionRuntimePort(
        db=AsyncMock(),
        llm_service=llm,
        workspace_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
    )


@pytest.fixture(autouse=True)
def _reset_quota_factory():
    yield
    set_quota_service_factory(None)


_MESSAGES = [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "Hello world"},
]


@pytest.mark.asyncio
async def test_cost_is_chunk_invariant_without_provider_usage() -> None:
    """Big-chunk and small-chunk streams of identical content cost the same."""
    quota_big = _FakeQuotaService()
    port_big = _make_port(_FakeLLM([{"content": "abcdefgh" * 8}]), quota_big)
    async for _ in port_big.llm_invoke(_MESSAGES, {"model": "m"}):
        pass

    quota_small = _FakeQuotaService()
    small_chunks = [{"content": c} for c in ["a", "b", "c", "d"] * 16]  # single-char chunks
    port_small = _make_port(_FakeLLM(small_chunks), quota_small)
    async for _ in port_small.llm_invoke(_MESSAGES, {"model": "m"}):
        pass

    assert quota_big.recorded, "cost should be recorded"
    assert quota_big.recorded[0]["amount"] == quota_small.recorded[0]["amount"]
    assert quota_big.recorded[0]["amount"] > 0


@pytest.mark.asyncio
async def test_provider_usage_takes_precedence_over_estimate() -> None:
    """When the stream carries usage, recorded cost derives from it."""
    quota = _FakeQuotaService()
    llm = _FakeLLM(
        [
            {"content": "answer"},
            {
                "content": None,
                "tool_calls": None,
                "finish_reason": None,
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                },
            },
        ]
    )
    port = _make_port(llm, quota)
    async for _ in port.llm_invoke(_MESSAGES, {"model": "m"}):
        pass

    # amount = (100 + 20) * 0.00001
    assert quota.recorded[0]["amount"] == pytest.approx(120 * 0.00001)


@pytest.mark.asyncio
async def test_tool_only_invocation_records_nonzero_input_usage() -> None:
    """A tool_calls-only round trip still bills the input side."""
    quota = _FakeQuotaService()
    llm = _FakeLLM([{"content": None, "tool_calls": [{"id": "1", "function": {"name": "search"}}]}])
    port = _make_port(llm, quota)
    async for _ in port.llm_invoke(_MESSAGES, {"model": "m"}):
        pass

    assert quota.recorded, "tool-only invocation must record usage"
    assert quota.recorded[0]["amount"] > 0


@pytest.mark.asyncio
async def test_model_limit_parameters_reach_chat_stream() -> None:
    """temperature/max_tokens/timeout/num_retries are forwarded verbatim."""
    quota = _FakeQuotaService()
    llm = _FakeLLM([{"content": "ok"}])
    port = _make_port(llm, quota)
    config = {"model": "m", "temperature": 0.2, "max_tokens": 512, "timeout": 30, "num_retries": 2}
    async for _ in port.llm_invoke(_MESSAGES, config):
        pass

    kwargs = llm.calls[0]
    assert kwargs["temperature"] == 0.2
    assert kwargs["max_tokens"] == 512
    assert kwargs["timeout"] == 30
    assert kwargs["num_retries"] == 2


@pytest.mark.asyncio
async def test_arbitrary_config_keys_are_not_forwarded() -> None:
    """Unknown config keys stay out of the provider call."""
    llm = _FakeLLM([{"content": "ok"}])
    port = _make_port(llm, _FakeQuotaService())
    async for _ in port.llm_invoke(_MESSAGES, {"model": "m", "routing_config": {"x": 1}}):
        pass

    assert "routing_config" not in llm.calls[0]
