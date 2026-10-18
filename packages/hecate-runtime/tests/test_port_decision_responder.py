"""Port-backed decision responder behavior (scripted structured port).

Covers the conversion slice: construction-frozen inputs with pre-call
rejection, strict aggregation of both port chunk shapes, stable derived
action keys, single-call boundary under interruption/cancellation, usage
pass-through, and the responder→step→store chain with zero model calls on
recovery. Deterministic doubles only — no real-model quality claims.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import textwrap
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from hecate_runtime.accepted_decision import (
    FINAL_ANSWER_SCHEMA_VERSION,
    AcceptedDecisionRecord,
    AcceptedDecisionStore,
    DecisionConflictError,
    DecisionOwnership,
    DecisionStoreUnavailableError,
    RunRef,
    canonical_decision_digest,
)
from hecate_runtime.decision_step import (
    KIND_ACTIONS,
    KIND_FINAL_ANSWER,
    DecisionProposal,
    DecisionStep,
    DecisionValidationError,
)
from hecate_runtime.port_decision_responder import PortDecisionResponder
from hecate_runtime.ports import StubRuntimePort

REFS = {"config": "v1", "input": "digest-1"}
TOOL_SNAPSHOT: dict[str, Any] = {
    name: {"type": "function", "function": {"name": name, "description": "Test tool", "parameters": {"type": "object"}}}
    for name in ("biz_tool", "web_search")
}


def _run_ref() -> RunRef:
    return RunRef(issuer_domain="test-domain", run_id=uuid.uuid4().hex)


def _tool_call(name: str, arguments: Any, call_id: str = "call-ephemeral") -> dict[str, Any]:
    """One production-shaped aggregated entry (nested function object)."""
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


class StubStructuredPort(StubRuntimePort):
    """Deterministic structured chunks with call counting and closure tracking."""

    def __init__(
        self,
        chunks: list[dict[str, Any]],
        *,
        fail_at: int | None = None,
        cancel_at: int | None = None,
    ) -> None:
        super().__init__()
        self.calls = 0
        self.received: list[tuple[list[dict], dict]] = []
        self.stream_closed = False
        self._chunks = chunks
        self._fail_at = fail_at
        self._cancel_at = cancel_at

    async def llm_invoke_structured(self, messages: list[dict], config: dict) -> AsyncGenerator[dict[str, Any], None]:
        self.calls += 1
        self.received.append((json.loads(json.dumps(messages)), json.loads(json.dumps(config))))
        try:
            for index, chunk in enumerate(self._chunks):
                if self._fail_at is not None and index == self._fail_at:
                    raise RuntimeError("stream broke")
                if self._cancel_at is not None and index == self._cancel_at:
                    raise asyncio.CancelledError()
                yield chunk
        finally:
            self.stream_closed = True


class StubDecisionStore(AcceptedDecisionStore):
    """Local test double; the parameterized durable suite owns the real one."""

    def __init__(self, *, fail_commits: bool = False) -> None:
        self._records: dict[tuple[str, str, str], AcceptedDecisionRecord] = {}
        self._fail_commits = fail_commits

    async def lookup(self, run_ref: RunRef, decision_step_id: str) -> AcceptedDecisionRecord | None:
        return self._records.get((run_ref.issuer_domain, run_ref.run_id, decision_step_id))

    async def commit(
        self, record: AcceptedDecisionRecord, ownership: DecisionOwnership | None = None
    ) -> AcceptedDecisionRecord:
        if self._fail_commits:
            raise DecisionStoreUnavailableError("store down")
        digest = canonical_decision_digest(record)
        key = (record.run_ref.issuer_domain, record.run_ref.run_id, record.decision_step_id)
        existing = self._records.get(key)
        if existing is not None:
            if existing.content_digest != digest:
                raise DecisionConflictError(record.decision_step_id, existing.content_digest or "", digest)
            return existing
        stored = AcceptedDecisionRecord(
            run_ref=record.run_ref,
            decision_step_id=record.decision_step_id,
            input_refs=record.input_refs,
            accepted_output=record.accepted_output,
            actions=record.actions,
            accepted_at="2026-01-01T00:00:00+00:00",
            content_digest=digest,
            schema_version=record.schema_version,
        )
        self._records[key] = stored
        return stored


def _responder(
    port: StubRuntimePort,
    *,
    refs: dict[str, Any] | None = None,
    tools: dict[str, Any] | None = None,
    on_usage: Any = None,
) -> PortDecisionResponder:
    return PortDecisionResponder(
        port,
        messages=[{"role": "user", "content": "choose next action"}],
        config={"model": "test-model", "tools": list((tools if tools is not None else TOOL_SNAPSHOT).values())},
        tool_definitions=tools if tools is not None else TOOL_SNAPSHOT,
        expected_refs=refs if refs is not None else REFS,
        on_usage=on_usage,
    )


async def test_ref_mismatch_rejects_before_any_model_call() -> None:
    port = StubStructuredPort([{"content": None, "tool_calls": [_tool_call("biz_tool", "{}")]}])
    responder = _responder(port)
    with pytest.raises(DecisionValidationError):
        await responder.respond(_run_ref(), "step-1", {"config": "v2", "input": "digest-1"})
    assert port.calls == 0


async def test_empty_tool_snapshot_never_produces_actions_and_rejects_tool_calls() -> None:
    port = StubStructuredPort([{"content": None, "tool_calls": [_tool_call("biz_tool", "{}")]}])
    responder = _responder(port, tools={})
    with pytest.raises(DecisionValidationError):
        await responder.respond(_run_ref(), "step-1", dict(REFS))
    assert port.calls == 1  # the model call is allowed; the action path is what fails
    assert port.stream_closed


async def test_empty_tool_snapshot_allows_a_final_answer() -> None:
    port = StubStructuredPort([{"content": "no tools needed", "tool_calls": None}])
    proposal = await _responder(port, tools={}).respond(_run_ref(), "step-1", dict(REFS))
    assert proposal.kind == KIND_FINAL_ANSWER
    assert proposal.actions == ()
    assert proposal.accepted_output == {"text": "no tools needed"}
    assert port.calls == 1


async def test_multiple_actions_accumulate_without_duplicates() -> None:
    chunks = [
        {"content": "checking", "tool_calls": None},
        {"content": " stock", "tool_calls": None},
        {"content": None, "tool_calls": None, "usage": {"prompt_tokens": 10, "completion_tokens": 4}},
        {
            "content": None,
            "tool_calls": [
                _tool_call("web_search", '{"query": "stock"}', call_id="call-a"),
                _tool_call("biz_tool", '{"q": 1}', call_id="call-b"),
                _tool_call("biz_tool", '{"q": 2}', call_id="call-c"),
            ],
        },
    ]
    port = StubStructuredPort(chunks)
    proposal = await _responder(port).respond(_run_ref(), "step-1", dict(REFS))
    assert isinstance(proposal, DecisionProposal)
    assert proposal.kind == KIND_ACTIONS  # text alongside tool calls stays an action decision
    assert proposal.accepted_output == {"text": "checking stock"}
    assert [action.tool for action in proposal.actions] == ["web_search", "biz_tool", "biz_tool"]
    assert [action.arguments for action in proposal.actions] == [{"query": "stock"}, {"q": 1}, {"q": 2}]
    keys = [action.action_key for action in proposal.actions]
    assert len(set(keys)) == len(keys)
    assert all("call-" not in key for key in keys)  # provider ephemeral ids never leak into keys
    assert [action.side_effect_class for action in proposal.actions] == ["readonly", "unknown", "unknown"]


async def test_defensive_tool_calls_chunks_take_the_last_accumulated_list() -> None:
    chunks = [
        {"content": None, "tool_calls": [_tool_call("biz_tool", '{"q": 0}')]},
        {"content": None, "tool_calls": [_tool_call("biz_tool", '{"q": 1}'), _tool_call("web_search", "{}")]},
    ]
    proposal = await _responder(StubStructuredPort(chunks)).respond(_run_ref(), "step-1", dict(REFS))
    assert [action.arguments for action in proposal.actions] == [{"q": 1}, {}]


async def test_text_only_without_tool_calls_becomes_a_final_answer() -> None:
    chunks = [{"content": "plain answer", "tool_calls": None}]
    proposal = await _responder(StubStructuredPort(chunks)).respond(_run_ref(), "step-1", dict(REFS))
    assert proposal.kind == KIND_FINAL_ANSWER
    assert proposal.actions == ()
    assert proposal.accepted_output == {"text": "plain answer"}


async def test_default_port_full_text_shape_yields_a_final_answer() -> None:
    # The default RuntimePort implementation yields one full-text chunk with
    # tool_calls=None; nonblank text becomes an accepted final-answer proposal.
    proposal = await _responder(StubRuntimePort()).respond(_run_ref(), "step-1", dict(REFS))
    assert proposal.kind == KIND_FINAL_ANSWER
    assert proposal.accepted_output == {"text": "stub-response"}


async def test_blank_text_without_tool_calls_fails_explicitly() -> None:
    # A response with neither tool calls nor nonblank text is invalid,
    # never an empty answer.
    for text in ("", "   "):
        chunks = [{"content": text, "tool_calls": None}]
        with pytest.raises(DecisionValidationError):
            await _responder(StubStructuredPort(chunks)).respond(_run_ref(), "step-1", dict(REFS))


async def test_tool_calls_vanishing_from_the_final_aggregation_fail() -> None:
    # Tool intent appeared in the stream; a text-only final aggregation must
    # fail instead of downgrading the response to a successful answer.
    chunks = [
        {"content": None, "tool_calls": [_tool_call("biz_tool", '{"q": 1}')]},
        {"content": "answer without tools", "tool_calls": []},
    ]
    port = StubStructuredPort(chunks)
    with pytest.raises(DecisionValidationError):
        await _responder(port).respond(_run_ref(), "step-1", dict(REFS))
    assert port.calls == 1
    assert port.stream_closed


@pytest.mark.parametrize(
    "arguments",
    ["[1, 2]", '"scalar"', "42", '{"broken"', ["not", "a", "string"], 17, None],
)
async def test_invalid_arguments_fail_explicitly(arguments: Any) -> None:
    chunks = [{"content": None, "tool_calls": [_tool_call("biz_tool", arguments=arguments)]}]
    with pytest.raises(DecisionValidationError):
        await _responder(StubStructuredPort(chunks)).respond(_run_ref(), "step-1", dict(REFS))


async def test_unknown_tool_name_fails_explicitly() -> None:
    chunks = [{"content": None, "tool_calls": [_tool_call("ghost_tool", "{}")]}]
    with pytest.raises(DecisionValidationError):
        await _responder(StubStructuredPort(chunks)).respond(_run_ref(), "step-1", dict(REFS))


async def test_malformed_tool_call_entries_fail_explicitly() -> None:
    for entry in ("text", {"function": "not-an-object"}, {"name": "biz_tool"}):
        chunks: list[Any] = [{"content": None, "tool_calls": [entry]}]
        with pytest.raises(DecisionValidationError):
            await _responder(StubStructuredPort(chunks)).respond(_run_ref(), "step-1", dict(REFS))


async def test_action_keys_are_stable_for_the_same_identity() -> None:
    run_ref = _run_ref()
    chunks_a = [{"content": None, "tool_calls": [_tool_call("biz_tool", '{"q": 1}', call_id="ephemeral-1")]}]
    chunks_b = [{"content": None, "tool_calls": [_tool_call("biz_tool", '{"q": 1}', call_id="totally-different")]}]
    first = await _responder(StubStructuredPort(chunks_a)).respond(run_ref, "step-1", dict(REFS))
    second = await _responder(StubStructuredPort(chunks_b)).respond(run_ref, "step-1", dict(REFS))
    assert [a.action_key for a in first.actions] == [a.action_key for a in second.actions]


async def test_mid_stream_interruption_fails_closes_stream_and_never_retries() -> None:
    chunks = [
        {"content": "partial", "tool_calls": None},
        {"content": None, "tool_calls": [_tool_call("biz_tool", "{}")]},
    ]
    port = StubStructuredPort(chunks, fail_at=1)
    with pytest.raises(RuntimeError, match="stream broke"):
        await _responder(port).respond(_run_ref(), "step-1", dict(REFS))
    assert port.calls == 1
    assert port.stream_closed is True


async def test_cancellation_propagates_and_closes_stream() -> None:
    chunks = [
        {"content": "partial", "tool_calls": None},
        {"content": None, "tool_calls": [_tool_call("biz_tool", "{}")]},
    ]
    port = StubStructuredPort(chunks, cancel_at=1)
    with pytest.raises(asyncio.CancelledError):
        await _responder(port).respond(_run_ref(), "step-1", dict(REFS))
    assert port.calls == 1
    assert port.stream_closed is True


async def test_full_chain_commits_and_recovery_hits_zero_model_calls() -> None:
    store = StubDecisionStore()
    run_ref = _run_ref()
    chunks = [
        {"content": "querying", "tool_calls": None},
        {"content": None, "tool_calls": [_tool_call("biz_tool", '{"q": 7}')]},
    ]
    port = StubStructuredPort(chunks)
    first = await DecisionStep(store, _responder(port)).execute(
        run_ref=run_ref, decision_step_id="step-1", input_refs=dict(REFS)
    )
    assert first.resumed is False
    assert first.record.accepted_output == {"text": "querying"}
    assert first.record.actions[0].arguments == {"q": 7}
    assert port.calls == 1

    recovery_port = StubStructuredPort(chunks)
    second = await DecisionStep(store, _responder(recovery_port)).execute(
        run_ref=run_ref, decision_step_id="step-1", input_refs=dict(REFS)
    )
    assert second.resumed is True
    assert second.record == first.record
    assert recovery_port.calls == 0  # original decision returned, model never re-asked


async def test_full_chain_final_answer_commits_and_recovers_with_zero_model_calls() -> None:
    store = StubDecisionStore()
    run_ref = _run_ref()
    chunks = [{"content": "the answer", "tool_calls": None}]
    port = StubStructuredPort(chunks)
    first = await DecisionStep(store, _responder(port)).execute(
        run_ref=run_ref, decision_step_id="step-1", input_refs=dict(REFS)
    )
    assert first.resumed is False
    assert first.record.schema_version == FINAL_ANSWER_SCHEMA_VERSION
    assert first.record.accepted_output == {"text": "the answer"}
    assert first.record.actions == ()
    assert port.calls == 1

    recovery_port = StubStructuredPort(chunks)
    second = await DecisionStep(store, _responder(recovery_port)).execute(
        run_ref=run_ref, decision_step_id="step-1", input_refs=dict(REFS)
    )
    assert second.resumed is True
    assert second.record == first.record
    assert recovery_port.calls == 0  # accepted final answer returned, model never re-asked


async def test_commit_failure_does_not_trigger_a_second_model_call() -> None:
    store = StubDecisionStore(fail_commits=True)
    port = StubStructuredPort([{"content": None, "tool_calls": [_tool_call("biz_tool", "{}")]}])
    with pytest.raises(DecisionStoreUnavailableError):
        await DecisionStep(store, _responder(port)).execute(
            run_ref=_run_ref(), decision_step_id="step-1", input_refs=dict(REFS)
        )
    assert port.calls == 1


async def test_resume_with_changed_input_versions_conflicts_without_model_call() -> None:
    store = StubDecisionStore()
    run_ref = _run_ref()
    port = StubStructuredPort([{"content": None, "tool_calls": [_tool_call("biz_tool", "{}")]}])
    await DecisionStep(store, _responder(port)).execute(
        run_ref=run_ref, decision_step_id="step-1", input_refs=dict(REFS)
    )
    changed_refs_port = StubStructuredPort([{"content": None, "tool_calls": [_tool_call("biz_tool", "{}")]}])
    with pytest.raises(DecisionConflictError):
        await DecisionStep(store, _responder(changed_refs_port)).execute(
            run_ref=run_ref, decision_step_id="step-1", input_refs={"config": "v2", "input": "digest-1"}
        )
    assert changed_refs_port.calls == 0


async def test_usage_forwarded_once_verbatim_and_never_enters_the_record() -> None:
    usage_reports: list[dict[str, Any]] = []
    chunks = [
        {"content": "hi", "tool_calls": None},
        {"content": None, "tool_calls": None, "usage": {"prompt_tokens": 11, "completion_tokens": 5}},
        {"content": None, "tool_calls": [_tool_call("biz_tool", '{"q": 1}')]},
    ]
    port = StubStructuredPort(chunks)
    store = StubDecisionStore()
    outcome = await DecisionStep(store, _responder(port, on_usage=usage_reports.append)).execute(
        run_ref=_run_ref(), decision_step_id="step-1", input_refs=dict(REFS)
    )
    assert usage_reports == [{"prompt_tokens": 11, "completion_tokens": 5}]
    assert "usage" not in outcome.record.accepted_output
    assert outcome.record.accepted_output == {"text": "hi"}
    record_dict = outcome.record.to_dict()
    assert "usage" not in record_dict
    assert "usage" not in record_dict["accepted_output"]
    assert all("usage" not in action.to_dict() for action in outcome.record.actions)


async def test_no_usage_callback_is_safe() -> None:
    chunks = [
        {"content": None, "tool_calls": None, "usage": {"prompt_tokens": 1}},
        {"content": None, "tool_calls": [_tool_call("biz_tool", "{}")]},
    ]
    proposal = await _responder(StubStructuredPort(chunks)).respond(_run_ref(), "step-1", dict(REFS))
    assert proposal.actions[0].tool == "biz_tool"


async def test_final_answer_usage_forwarded_once_and_never_enters_the_record() -> None:
    reports: list[dict[str, Any]] = []
    chunks = [
        {"content": "final", "tool_calls": None},
        {"content": None, "tool_calls": None, "usage": {"prompt_tokens": 7, "completion_tokens": 2}},
    ]
    proposal = await _responder(StubStructuredPort(chunks), on_usage=reports.append).respond(
        _run_ref(), "step-1", dict(REFS)
    )
    assert reports == [{"prompt_tokens": 7, "completion_tokens": 2}]
    assert proposal.kind == KIND_FINAL_ANSWER
    assert proposal.accepted_output == {"text": "final"}
    assert "usage" not in proposal.accepted_output


async def test_construction_freezes_nested_inputs_and_version_references() -> None:
    messages = [{"role": "user", "content": "original"}]
    config: dict[str, Any] = {"model": "original", "options": {"temperature": 0}}
    tools = json.loads(json.dumps(TOOL_SNAPSHOT))
    refs = {"input": {"version": "v1"}}
    port = StubStructuredPort([{"tool_calls": [_tool_call("biz_tool", "{}")]}])
    responder = PortDecisionResponder(
        port, messages=messages, config=config, tool_definitions=tools, expected_refs=refs
    )
    messages[0]["content"] = "changed"
    config["options"]["temperature"] = 1
    refs["input"]["version"] = "v2"
    tools["biz_tool"]["function"]["description"] = "changed"
    await responder.respond(_run_ref(), "step-1", {"input": {"version": "v1"}})
    assert port.received[0][0] == [{"role": "user", "content": "original"}]
    assert port.received[0][1]["options"] == {"temperature": 0}
    assert port.received[0][1]["tools"] == list(TOOL_SNAPSHOT.values())
    with pytest.raises(DecisionValidationError):
        await responder.respond(_run_ref(), "step-1", refs)
    assert port.calls == 1


async def test_mutating_host_tool_snapshot_cannot_expand_callable_tools() -> None:
    tools = json.loads(json.dumps(TOOL_SNAPSHOT))
    port = StubStructuredPort([{"tool_calls": [_tool_call("ghost_tool", "{}")]}])
    responder = _responder(port, tools=tools)
    tools["ghost_tool"] = {"type": "function", "function": {"name": "ghost_tool"}}
    with pytest.raises(DecisionValidationError):
        await responder.respond(_run_ref(), "step-1", dict(REFS))


async def test_port_receives_a_fresh_copy_on_each_call() -> None:
    class MutatingPort(StubStructuredPort):
        async def llm_invoke_structured(self, messages: list[dict], config: dict) -> AsyncGenerator[dict, None]:
            async for chunk in super().llm_invoke_structured(messages, config):
                messages[0]["content"] = "changed by provider"
                config["tools"].clear()
                yield chunk

    port = MutatingPort([{"tool_calls": [_tool_call("biz_tool", "{}")]}])
    responder = _responder(port)
    await responder.respond(_run_ref(), "one", dict(REFS))
    await responder.respond(_run_ref(), "two", dict(REFS))
    assert port.received[0] == port.received[1]
    assert port.received[1][1]["tools"] == list(TOOL_SNAPSHOT.values())


@pytest.mark.parametrize("tool_calls", [[], {}, "bad", False, 3, ()])
async def test_invalid_aggregate_never_returns_a_proposal(tool_calls: Any) -> None:
    port = StubStructuredPort([{"tool_calls": tool_calls}])
    with pytest.raises(DecisionValidationError):
        await _responder(port).respond(_run_ref(), "step-1", dict(REFS))
    assert port.calls == 1
    assert port.stream_closed


@pytest.mark.parametrize(
    "arguments",
    ['{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}', '{"x":1e999}', {"x": float("nan")}, {1: "x"}],
)
async def test_nonfinite_or_lossy_arguments_are_rejected_by_responder(arguments: Any) -> None:
    port = StubStructuredPort([{"tool_calls": [_tool_call("biz_tool", arguments)]}])
    with pytest.raises(DecisionValidationError):
        await _responder(port).respond(_run_ref(), "step-1", dict(REFS))


@pytest.mark.parametrize("chunk", [None, [], {"content": 123}, {"content": {"text": "bad"}}])
async def test_malformed_chunks_do_not_silently_discard_visible_output(chunk: Any) -> None:
    port = StubStructuredPort([chunk, {"tool_calls": [_tool_call("biz_tool", "{}")]}])
    with pytest.raises(DecisionValidationError):
        await _responder(port).respond(_run_ref(), "step-1", dict(REFS))
    assert port.stream_closed


async def test_model_tools_are_bound_to_the_validated_snapshot() -> None:
    port = StubStructuredPort([{"tool_calls": [_tool_call("biz_tool", "{}")]}])
    responder = PortDecisionResponder(
        port, messages=[], config={"model": "test"}, tool_definitions=TOOL_SNAPSHOT, expected_refs=REFS
    )
    await responder.respond(_run_ref(), "step-1", dict(REFS))
    assert port.received[0][1]["tools"] == list(TOOL_SNAPSHOT.values())


async def test_conflicting_config_tools_reject_before_model_call() -> None:
    port = StubStructuredPort([{"tool_calls": [_tool_call("biz_tool", "{}")]}])
    with pytest.raises(DecisionValidationError):
        responder = PortDecisionResponder(
            port,
            messages=[],
            config={"tools": [{"type": "function", "function": {"name": "ghost_tool"}}]},
            tool_definitions=TOOL_SNAPSHOT,
            expected_refs=REFS,
        )
        await responder.respond(_run_ref(), "step-1", dict(REFS))
    assert port.calls == 0


async def test_provider_cannot_mutate_an_already_emitted_aggregate() -> None:
    class ReusingPort(StubStructuredPort):
        async def llm_invoke_structured(self, messages: list[dict], config: dict) -> AsyncGenerator[dict, None]:
            arguments = {"nested": {"value": "original"}}
            calls = [_tool_call("biz_tool", arguments)]
            yield {"tool_calls": calls}
            arguments["nested"]["value"] = "changed"
            calls.clear()
            yield {"content": "done", "tool_calls": None}

    proposal = await _responder(ReusingPort([])).respond(_run_ref(), "step-1", dict(REFS))
    assert len(proposal.actions) == 1
    assert proposal.actions[0].arguments == {"nested": {"value": "original"}}


async def test_real_task_cancellation_closes_stream_and_never_commits_partial_actions() -> None:
    ready = asyncio.Event()
    closed = asyncio.Event()

    class BlockingPort(StubRuntimePort):
        async def llm_invoke_structured(self, messages: list[dict], config: dict) -> AsyncGenerator[dict, None]:
            try:
                yield {"tool_calls": [_tool_call("biz_tool", "{}")]}
                ready.set()
                await asyncio.Event().wait()
            finally:
                closed.set()

    store = StubDecisionStore()
    run_ref = _run_ref()
    task = asyncio.create_task(
        DecisionStep(store, _responder(BlockingPort())).execute(
            run_ref=run_ref, decision_step_id="step-1", input_refs=dict(REFS)
        )
    )
    await asyncio.wait_for(ready.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()
    assert await store.lookup(run_ref, "step-1") is None


async def test_error_after_emitting_actions_does_not_accept_them() -> None:
    port = StubStructuredPort([{"tool_calls": [_tool_call("biz_tool", "{}")]}, {"content": "ignored"}], fail_at=1)
    store = StubDecisionStore()
    run_ref = _run_ref()
    with pytest.raises(RuntimeError, match="stream broke"):
        await DecisionStep(store, _responder(port)).execute(
            run_ref=run_ref, decision_step_id="step-1", input_refs=dict(REFS)
        )
    assert await store.lookup(run_ref, "step-1") is None
    assert port.calls == 1
    assert port.stream_closed


@pytest.mark.parametrize("step", ["", " ", "s" * 257, None])
async def test_invalid_step_identity_is_rejected_before_model_call(step: Any) -> None:
    port = StubStructuredPort([{"tool_calls": [_tool_call("biz_tool", "{}")]}])
    with pytest.raises(DecisionValidationError):
        await _responder(port).respond(_run_ref(), step, dict(REFS))
    assert port.calls == 0


async def test_action_keys_are_bounded_and_scoped_to_all_identity_fields() -> None:
    port = StubStructuredPort([{"tool_calls": [_tool_call("biz_tool", "{}"), _tool_call("biz_tool", "{}")]}])
    responder = _responder(port)
    identities = [
        (RunRef("a", "r"), "s"),
        (RunRef("b", "r"), "s"),
        (RunRef("a", "q"), "s"),
        (RunRef("a", "r"), "t"),
        (RunRef("d" * 256, "r" * 256), "s" * 256),
    ]
    keys: list[str] = []
    for run_ref, step in identities:
        proposal = await responder.respond(run_ref, step, dict(REFS))
        keys.extend(action.action_key for action in proposal.actions)
    assert len(set(keys)) == len(keys)
    assert all(len(key) <= 256 for key in keys)


@pytest.mark.parametrize(
    "definitions",
    [
        {"wrong": {"type": "function", "function": {"name": "biz_tool"}}},
        {"": {"type": "function", "function": {"name": ""}}},
        {"biz_tool": {"type": "function", "function": "bad"}},
        {"biz_tool": {"type": "other", "function": {"name": "biz_tool"}}},
    ],
)
async def test_invalid_tool_snapshots_are_rejected_before_model_call(definitions: Any) -> None:
    port = StubStructuredPort([])
    with pytest.raises(DecisionValidationError):
        responder = _responder(port, tools=definitions)
        await responder.respond(_run_ref(), "step", dict(REFS))
    assert port.calls == 0


async def test_usage_and_visible_output_are_detached_from_vendor_metadata() -> None:
    class ReusingPort(StubRuntimePort):
        async def llm_invoke_structured(self, messages: list[dict], config: dict) -> AsyncGenerator[dict, None]:
            usage = {"prompt_tokens": 3}
            yield {"usage": usage}
            usage["prompt_tokens"] = 999
            entry = _tool_call("biz_tool", "{}")
            entry["vendor_reasoning"] = object()
            yield {"content": "visible", "tool_calls": [entry], "reasoning": object()}

    reports: list[dict] = []
    proposal = await _responder(ReusingPort(), on_usage=reports.append).respond(_run_ref(), "s", dict(REFS))
    assert reports == [{"prompt_tokens": 3}]
    assert proposal.accepted_output == {"text": "visible"}
    assert proposal.actions[0].arguments == {}


def test_responder_runs_in_a_fresh_process_with_graph_and_domains_blocked() -> None:
    probe = textwrap.dedent("""
        import asyncio
        import importlib.abc
        import sys
        forbidden = ("hecate_runtime.pregel", "hecate_runtime.worker", "hecate_runtime.workers",
                     "hecate_runtime.compiler", "hecate_runtime.channel", "hecate", "hecate_durable",
                     "hecate_runner", "hecate_governance", "hecate_llm", "hecate_ops")
        class Blocker(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if any(fullname == prefix or fullname.startswith(prefix + ".") for prefix in forbidden):
                    raise AssertionError("Forbidden import: " + fullname)
        sys.meta_path.insert(0, Blocker())
        from hecate_runtime.port_decision_responder import PortDecisionResponder
        from hecate_runtime.ports import StubRuntimePort
        from hecate_runtime.accepted_decision import RunRef
        class StubPort(StubRuntimePort):
            async def llm_invoke_structured(self, messages, config):
                yield {"content": "visible", "tool_calls": [
                    {"function": {"name": "read_file", "arguments": '{"path":"a"}'}}]}
            async def tool_execute(self, *args, **kwargs):
                raise AssertionError("Responder dispatched a tool")
        async def main():
            responder = PortDecisionResponder(StubPort(), messages=[], config={}, expected_refs={},
                tool_definitions={"read_file": {"type": "function", "function": {"name": "read_file"}}})
            result = await responder.respond(RunRef("test", "run"), "step", {})
            assert result.actions[0].arguments == {"path": "a"}
            assert result.actions[0].side_effect_class == "readonly"
            assert result.accepted_output == {"text": "visible"}
        asyncio.run(main())
    """)
    result = subprocess.run(  # noqa: S603 — trusted interpreter and constant probe; no shell
        [sys.executable, "-I", "-c", probe], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("tools", [{}, TOOL_SNAPSHOT])
@pytest.mark.parametrize(
    "chunks",
    [
        [{"content": "answer", "tool_calls": []}],
        [{"tool_calls": []}, {"content": "ans"}, {"content": "wer", "tool_calls": []}],
    ],
)
async def test_empty_tool_call_lists_without_prior_intent_allow_final_answer(tools, chunks) -> None:
    """An empty aggregate contains no tool intent, even with an empty snapshot."""
    store = StubDecisionStore()
    port = StubStructuredPort(chunks)
    outcome = await DecisionStep(store, _responder(port, tools=tools)).execute(
        run_ref=_run_ref(), decision_step_id="final", input_refs=dict(REFS)
    )
    assert outcome.record.schema_version == FINAL_ANSWER_SCHEMA_VERSION
    assert outcome.record.accepted_output == {"text": "answer"}
    assert outcome.record.actions == ()
    assert port.calls == 1
    assert port.stream_closed
