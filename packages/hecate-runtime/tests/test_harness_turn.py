"""Per-turn harness pipeline over the frozen decision responder.

Covers the P1 composition order end to end at the responder seam: a
pre-call guard BLOCK refuses the call before any model usage, SANITIZE
replaces the messages the model sees, the context processor chain
projects the conversation non-destructively, provider shaping rewrites
what the port receives, output governance BLOCK refuses a completed
response and SANITIZE rewrites the accepted text, and controlled
termination stops before another model call. With nothing wired the
pipeline preserves the un-pipelined responder's model inputs.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from hecate_runtime.accepted_decision import DecisionValidationError, canonical_json
from hecate_runtime.context_policy import ContextChainFactory
from hecate_runtime.context_processors import (
    LEVEL_TERMINATE,
    ContextProcessor,
    ContextProcessorChain,
    ContextUnit,
    FailurePolicy,
    ProcessorResult,
    TerminationProcessor,
)
from hecate_runtime.guardrail import GuardrailAction, GuardrailResult, PostLLMHook, PreLLMHook
from hecate_runtime.harness_turn import HarnessTurnPipeline, ModelCallRejectedError
from hecate_runtime.middleware_factory import build_llm_chain
from hecate_runtime.ports import StubRuntimePort
from test_accepted_action_step import _run_ref


class _RecordingPort(StubRuntimePort):
    """Structured model double that records what the pipeline delivered."""

    def __init__(self, *, answer: str = "ok", tool_answer: bool = False) -> None:
        super().__init__()
        self.answer = answer
        self.tool_answer = tool_answer
        self.calls: list[dict[str, Any]] = []

    async def llm_invoke_structured(self, messages: list[dict], config: dict):
        self.calls.append({"messages": messages, "config": config})
        if self.tool_answer:
            yield {
                "content": "working",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "query_inventory", "arguments": "{}"},
                    }
                ],
            }
        else:
            yield {"content": self.answer, "tool_calls": None}


class _ShapingPort(_RecordingPort):
    """Provider shaping double: prefix every message content."""

    async def context_assemble(self, messages, tools, session_id, model="gpt-4o"):
        shaped = [{**message, "content": f"[shaped] {message.get('content')}"} for message in messages]
        return {"messages": shaped, "tools": tools}


class _BlockingPreHook(PreLLMHook):
    async def on_pre_llm_call(self, messages, model, tools):
        return GuardrailResult(action=GuardrailAction.BLOCK, reason="prompt injection suspected")


class _SanitizingPreHook(PreLLMHook):
    async def on_pre_llm_call(self, messages, model, tools):
        return GuardrailResult(
            action=GuardrailAction.SANITIZE,
            modified_data={
                "messages": [{**m, "content": m["content"].replace("SECRET", "[redacted]")} for m in messages]
            },
        )


class _BlockingPostHook(PostLLMHook):
    async def on_post_llm_call(self, response, messages):
        return GuardrailResult(action=GuardrailAction.BLOCK, reason="unsafe output")


class _SanitizingPostHook(PostLLMHook):
    async def on_post_llm_call(self, response, messages):
        return GuardrailResult(
            action=GuardrailAction.SANITIZE,
            modified_data={"response": {**response, "content": response["content"].replace("BAD", "[clean]")}},
        )


class _MarkingProcessor(ContextProcessor):
    """Append one marker message — a visible, deterministic projection."""

    name = "test_marker"
    run_mode = "always"

    async def process(self, units: list[ContextUnit], ctx) -> tuple[list[ContextUnit], ProcessorResult]:
        marker = ContextUnit([{"role": "system", "content": "projected-by-chain"}])
        return units + [marker], ProcessorResult(processor=self.name)


class _TerminatingProcessor(TerminationProcessor):
    """Controlled termination verdict regardless of the actual budget."""

    name = "test_terminate"
    run_mode = "always"

    async def process(self, units: list[ContextUnit], ctx) -> tuple[list[ContextUnit], ProcessorResult]:
        return units, ProcessorResult(
            processor=self.name, level=LEVEL_TERMINATE, metadata={"stop_reason": self.stop_reason}
        )


def _responder(port: StubRuntimePort, pipeline: HarnessTurnPipeline | None = None):
    from hecate_runtime.port_decision_responder import PortDecisionResponder

    return PortDecisionResponder(
        port,
        messages=[{"role": "user", "content": "hello SECRET"}],
        config={"model": "stub"},
        tool_definitions={"query_inventory": {"type": "function", "function": {"name": "query_inventory"}}},
        expected_refs={"model": "stub"},
        pipeline=pipeline,
    )


async def _respond(pipeline: HarnessTurnPipeline | None = None, **port_kwargs):
    port = _RecordingPort(**port_kwargs)
    proposal = await _responder(port, pipeline).respond(_run_ref(), "step-1", {"model": "stub"})
    return port, proposal


async def test_pipeline_absent_behaves_like_the_frozen_responder() -> None:
    port, proposal = await _respond(answer="plain answer")
    assert proposal.accepted_output["text"] == "plain answer"
    assert port.calls[0]["messages"] == [{"role": "user", "content": "hello SECRET"}]


async def test_pre_call_block_refuses_before_any_model_usage() -> None:
    chains = build_llm_chain(pre_hook=_BlockingPreHook(), post_hook=None)
    port = _RecordingPort()
    with pytest.raises(ModelCallRejectedError) as excinfo:
        await _responder(port, HarnessTurnPipeline(middleware_chains=chains, model="stub")).respond(
            _run_ref(), "step-1", {"model": "stub"}
        )
    assert excinfo.value.stage == "pre_llm"
    assert excinfo.value.reason == "prompt injection suspected"
    assert port.calls == []  # refused before the port was invoked


async def test_pre_call_sanitize_redacts_what_the_model_sees() -> None:
    chains = build_llm_chain(pre_hook=_SanitizingPreHook(), post_hook=None)
    port, _proposal = await _respond(answer="ok", pipeline=HarnessTurnPipeline(middleware_chains=chains, model="stub"))
    assert port.calls[0]["messages"] == [{"role": "user", "content": "hello [redacted]"}]


async def test_context_chain_projects_the_invocation_view() -> None:
    chain = ContextProcessorChain(processors=[_MarkingProcessor()], failure_policy=FailurePolicy())
    port, _proposal = await _respond(pipeline=HarnessTurnPipeline(context_chain=chain))
    contents = [message["content"] for message in port.calls[0]["messages"]]
    assert contents == ["hello SECRET", "projected-by-chain"]


async def test_provider_shaping_rewrites_the_port_invocation() -> None:
    shaping_port = _ShapingPort()
    chain = ContextProcessorChain(processors=[_MarkingProcessor()], failure_policy=FailurePolicy())
    pipeline = HarnessTurnPipeline(context_chain=chain, port=shaping_port, model="stub")
    port, proposal = await _respond(answer="final text", pipeline=pipeline)
    # The shaping capability never invokes the model itself; the model port
    # received the shaped projection and the accepted text is untouched.
    assert shaping_port.calls == []
    assert port.calls[0]["messages"] == [
        {"role": "user", "content": "[shaped] hello SECRET"},
        {"role": "system", "content": "[shaped] projected-by-chain"},
    ]
    assert proposal.accepted_output["text"] == "final text"


async def test_output_governance_block_refuses_the_response() -> None:
    chains = build_llm_chain(pre_hook=None, post_hook=_BlockingPostHook())
    port = _RecordingPort(answer="dangerous")
    with pytest.raises(ModelCallRejectedError) as excinfo:
        await _responder(port, HarnessTurnPipeline(middleware_chains=chains, model="stub")).respond(
            _run_ref(), "step-1", {"model": "stub"}
        )
    assert excinfo.value.stage == "llm_response"
    assert excinfo.value.reason == "unsafe output"
    assert port.calls  # the stream ran; the port already accounted the usage


async def test_output_governance_sanitize_rewrites_the_accepted_text() -> None:
    chains = build_llm_chain(pre_hook=None, post_hook=_SanitizingPostHook())
    _port, proposal = await _respond(answer="say BAD things", pipeline=HarnessTurnPipeline(middleware_chains=chains))
    assert proposal.accepted_output["text"] == "say [clean] things"


async def test_controlled_termination_refuses_model_usage_and_final_answer() -> None:
    chain = ContextProcessorChain(processors=[_TerminatingProcessor()], failure_policy=FailurePolicy())
    pipeline = HarnessTurnPipeline(context_chain=chain)
    port = _RecordingPort(tool_answer=True)
    with pytest.raises(ModelCallRejectedError) as excinfo:
        await _responder(port, pipeline).respond(_run_ref(), "step-1", {"model": "stub"})
    assert excinfo.value.stage == "context"
    assert excinfo.value.reason == "token_capped"
    assert port.calls == []


@pytest.mark.parametrize(
    "replacement",
    [None, [], [{"type": "function", "function": {"name": "query_inventory", "arguments": '{"safe":true}'}}]],
)
async def test_output_governance_replaces_tool_calls_without_restoring_originals(replacement) -> None:
    class Sanitizing(PostLLMHook):
        async def on_post_llm_call(self, response, messages):
            governed: dict[str, Any] = {"content": "governed"}
            if replacement is not None:
                governed["tool_calls"] = replacement
            return GuardrailResult(action=GuardrailAction.SANITIZE, modified_data={"response": governed})

    chains = build_llm_chain(pre_hook=None, post_hook=Sanitizing())
    _, proposal = await _respond(tool_answer=True, pipeline=HarnessTurnPipeline(middleware_chains=chains))
    if replacement:
        assert proposal.actions[0].arguments == {"safe": True}
    else:
        assert proposal.actions == ()
        assert proposal.kind == "final_answer"


@pytest.mark.parametrize("response", [{"content": None}, {"content": "safe", "tool_calls": "malformed"}])
async def test_malformed_output_sanitization_never_falls_back_to_original(response) -> None:
    class Malformed(PostLLMHook):
        async def on_post_llm_call(self, original, messages):
            return GuardrailResult(action=GuardrailAction.SANITIZE, modified_data={"response": response})

    with pytest.raises(ModelCallRejectedError, match="malformed response"):
        await _respond(
            tool_answer=True, pipeline=HarnessTurnPipeline(middleware_chains=build_llm_chain(None, Malformed()))
        )


async def test_request_governance_can_remove_provider_visible_tools() -> None:
    class FilterTools(PreLLMHook):
        async def on_pre_llm_call(self, messages, model, tools):
            return GuardrailResult(action=GuardrailAction.SANITIZE, modified_data={"messages": messages, "tools": []})

    port, _ = await _respond(pipeline=HarnessTurnPipeline(middleware_chains=build_llm_chain(FilterTools(), None)))
    assert port.calls[0]["config"]["tools"] == []


async def test_prepared_model_input_digest_binds_the_actual_projection() -> None:
    pipeline = HarnessTurnPipeline(context_chain=ContextProcessorChain([_MarkingProcessor()]))
    port, proposal = await _respond(pipeline=pipeline)
    assert (
        proposal.accepted_output["harness_input_sha256"]
        == hashlib.sha256(canonical_json(port.calls[0]).encode("utf-8")).hexdigest()
    )


@pytest.mark.parametrize(
    "policy", [[{"type": "compression", "params": {"backend": "surface_replacement"}}], ["offload"]]
)
def test_unsupported_storage_dependent_processors_are_explicitly_rejected(policy) -> None:
    with pytest.raises(DecisionValidationError, match="does not support"):
        HarnessTurnPipeline(context_chain=ContextChainFactory(agent_policy=policy))
