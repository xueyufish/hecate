"""Exercise decision conversion through the production structured port adapter."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any

import pytest
from hecate_runtime.accepted_decision import DecisionValidationError, RunRef
from hecate_runtime.decision_step import KIND_FINAL_ANSWER
from hecate_runtime.port_decision_responder import PortDecisionResponder
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.composition import runtime_port_adapter


class StubQuotaService:
    """Capture the existing adapter's settlement without another billing path."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record_usage(self, **kwargs: Any) -> None:
        """Record a quota update for assertions."""
        self.records.append(kwargs)


class StubLLMService:
    """Emit provider-shaped argument fragments through the real accumulator."""

    def __init__(self, *, invalid_arguments: bool) -> None:
        self.calls: list[dict[str, Any]] = []
        self._invalid_arguments = invalid_arguments

    async def chat_stream(self, **kwargs: Any) -> AsyncGenerator[dict[str, Any], None]:
        """Yield text, fragmented function calls, and terminal provider usage."""
        self.calls.append(kwargs)
        yield {"content": "Checking "}
        yield {
            "tool_calls": [
                SimpleNamespace(
                    index=0,
                    id="ephemeral",
                    type="function",
                    function=SimpleNamespace(name="biz_", arguments='{"q":'),
                )
            ]
        }
        yield {"content": "stock"}
        yield {
            "tool_calls": [
                SimpleNamespace(
                    index=0,
                    id=None,
                    type=None,
                    function=SimpleNamespace(name="tool", arguments="NaN}" if self._invalid_arguments else "7}"),
                )
            ]
        }
        yield {"usage": {"prompt_tokens": 13, "completion_tokens": 7, "total_tokens": 20}}


@pytest.mark.parametrize("invalid_arguments", [False, True])
async def test_production_stream_is_converted_and_accounted_once(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, invalid_arguments: bool
) -> None:
    quota = StubQuotaService()
    monkeypatch.setattr(runtime_port_adapter, "_quota_service_factory", lambda db, workspace_id: quota)
    service = StubLLMService(invalid_arguments=invalid_arguments)
    port = runtime_port_adapter._ProductionRuntimePort(db=db_session, llm_service=service, workspace_id=uuid.uuid4())
    definition = {"type": "function", "function": {"name": "biz_tool", "parameters": {"type": "object"}}}
    usage_reports: list[dict[str, Any]] = []
    responder = PortDecisionResponder(
        port,
        messages=[{"role": "user", "content": "Check stock"}],
        config={"model": "test-model", "temperature": 0.2, "max_tokens": 123},
        tool_definitions={"biz_tool": definition},
        expected_refs={"input": "v1"},
        on_usage=usage_reports.append,
    )
    if invalid_arguments:
        with pytest.raises(DecisionValidationError):
            await responder.respond(RunRef("test", "r"), "step", {"input": "v1"})
        assert usage_reports == []
    else:
        proposal = await responder.respond(RunRef("test", "r"), "step", {"input": "v1"})
        assert proposal.accepted_output == {"text": "Checking stock"}
        assert len(proposal.actions) == 1
        assert proposal.actions[0].tool == "biz_tool"
        assert proposal.actions[0].arguments == {"q": 7}
        assert proposal.actions[0].side_effect_class == "unknown"
        assert usage_reports == [{"prompt_tokens": 13, "completion_tokens": 7, "total_tokens": 20}]
    assert len(service.calls) == 1
    assert service.calls[0]["tools"] == [definition]
    assert service.calls[0]["temperature"] == 0.2
    assert service.calls[0]["max_tokens"] == 123
    # Conversion failure does not erase the cost of a completed model attempt.
    assert len(quota.records) == 1
    assert quota.records[0]["amount"] == pytest.approx(20 * 0.00001)


@pytest.mark.parametrize("with_tools", [False, True])
async def test_production_text_answer_is_converted_and_accounted_once(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, with_tools: bool
) -> None:
    """The real adapter's text/terminal-usage stream supports the final-answer exit."""

    class StubTextLLMService:
        """Emit a complete text response with provider-reported usage."""

        def __init__(self) -> None:
            self.calls = 0

        async def chat_stream(self, **kwargs: Any) -> AsyncGenerator[dict[str, Any], None]:
            """Yield text deltas and one terminal usage report."""
            self.calls += 1
            yield {"content": "Final "}
            yield {"content": "answer"}
            yield {"usage": {"prompt_tokens": 13, "completion_tokens": 7, "total_tokens": 20}}

    quota = StubQuotaService()
    monkeypatch.setattr(runtime_port_adapter, "_quota_service_factory", lambda db, workspace_id: quota)
    service = StubTextLLMService()
    port = runtime_port_adapter._ProductionRuntimePort(db=db_session, llm_service=service, workspace_id=uuid.uuid4())
    definitions = {"biz_tool": {"type": "function", "function": {"name": "biz_tool"}}} if with_tools else {}
    usage_reports: list[dict[str, Any]] = []
    proposal = await PortDecisionResponder(
        port,
        messages=[{"role": "user", "content": "Answer directly"}],
        config={"model": "test-model"},
        tool_definitions=definitions,
        expected_refs={"input": "v1"},
        on_usage=usage_reports.append,
    ).respond(RunRef("test", "r"), "step", {"input": "v1"})
    assert proposal.kind == KIND_FINAL_ANSWER
    assert proposal.accepted_output == {"text": "Final answer"}
    assert proposal.actions == ()
    assert service.calls == 1
    assert usage_reports == [{"prompt_tokens": 13, "completion_tokens": 7, "total_tokens": 20}]
    assert len(quota.records) == 1
    assert quota.records[0]["amount"] == pytest.approx(20 * 0.00001)
