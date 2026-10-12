"""Direct-loop stop semantics (step5e tasks 2.1-2.3, autonomous-execution-parity).

Covers: unknown tool outcome terminates the loop with needs_review and never
feeds the exception back to the model; budget refusal terminates with
budget_exhausted (distinct from the iteration cap); cancellation between
rounds stops before the next model call; replayed outcomes do not re-execute.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from hecate_governance.claims import OutcomeUnknownError

from hecate.channel.api.v1.chat import (
    _chat_with_tools,
    _execute_tool_calls,
    _LoopStopError,
)


def _llm_rounds(rounds: list[list[dict[str, Any]]]):
    """Scripted chat() returning one round per call (OpenAI-shaped)."""

    class _LLM:
        def __init__(self) -> None:
            self.calls = 0

        async def chat(self, **kwargs: Any) -> Any:
            idx = min(self.calls, len(rounds) - 1)
            self.calls += 1
            return rounds[idx]

    return _LLM()


def _tool_call_response(tool_call: dict[str, Any], content: str = "") -> Any:
    response = MagicMock()
    response.content = content
    response.tool_calls = [tool_call]
    return response


def _final_response(content: str) -> Any:
    response = MagicMock()
    response.content = content
    response.tool_calls = []
    return response


def _registry(execute=None, completed_result=None):
    registry = MagicMock()
    if execute is not None:
        registry.execute = execute
    if completed_result is not None:
        registry.completed_result = completed_result
    else:
        registry.completed_result = AsyncMock(return_value=(False, None))
    registry.describe = AsyncMock(return_value={})
    return registry


# parse_tool_calls output shape (flattened): id + name + dict arguments.
TOOL_CALL = {
    "id": "call_1",
    "name": "inventory_write",
    "arguments": {"sku": "42"},
}


class TestUnknownOutcomeStops:
    async def test_unknown_outcome_terminates_never_fed_back(self) -> None:
        # The governed registry raises OutcomeUnknownError for a protected
        # write whose outcome is not established: the loop must terminate
        # with needs_review, and the exception must NOT become a tool message.
        llm = _llm_rounds([_tool_call_response(TOOL_CALL), _final_response("done")])

        async def execute(name, args, context=None):
            raise OutcomeUnknownError("outcome not established")

        registry = _registry(execute=execute)
        with pytest.raises(_LoopStopError) as exc_info:
            await _chat_with_tools(
                [{"role": "user", "content": "write it"}],
                "m",
                [],
                registry,
                None,
                None,
                model_service=llm,
            )
        assert exc_info.value.reason_category == "needs_review"
        assert exc_info.value.action_ref is not None
        # Only ONE model round happened: the loop stopped before round 2.
        assert llm.calls == 1

    async def test_unknown_outcome_not_injected_into_history(self) -> None:

        async def execute(name, args, context=None):
            raise OutcomeUnknownError("outcome not established")

        captured: list = []

        async def chat(**kwargs: Any) -> Any:
            captured.append(list(kwargs["messages"]))
            return _tool_call_response(TOOL_CALL)

        llm = type("L", (), {"chat": staticmethod(chat)})()
        registry = _registry(execute=execute)
        with pytest.raises(_LoopStopError):
            await _chat_with_tools(
                [{"role": "user", "content": "write it"}],
                "m",
                [],
                registry,
                None,
                None,
                model_service=llm,
            )
        # No round-2 messages were captured (loop stopped), so the exception
        # never entered any conversation history.
        assert len(captured) == 1


class TestBudgetStop:
    async def test_budget_refusal_terminates_budget_exhausted(self) -> None:
        from hecate.core.composition.governance import BudgetRefusedError

        async def chat(**kwargs: Any) -> Any:
            raise BudgetRefusedError("metered model call refused: budget state exhausted")

        model_service = type("L", (), {"chat": staticmethod(chat)})()
        registry = _registry(execute=AsyncMock(return_value={"ok": True}))
        with pytest.raises(_LoopStopError) as exc_info:
            await _chat_with_tools(
                [{"role": "user", "content": "hi"}],
                "m",
                [],
                registry,
                None,
                None,
                model_service=model_service,
                max_iterations=5,
            )
        assert exc_info.value.reason_category == "budget_exhausted"


class TestIterationCapDistinct:
    async def test_iteration_cap_has_explicit_category(self) -> None:
        llm = _llm_rounds([_tool_call_response(TOOL_CALL)])
        registry = _registry(execute=AsyncMock(return_value={"ok": True}))
        with pytest.raises(_LoopStopError) as exc_info:
            await _chat_with_tools(
                [{"role": "user", "content": "hi"}],
                "m",
                [],
                registry,
                None,
                None,
                model_service=llm,
                max_iterations=1,
            )
        assert exc_info.value.reason_category == "iteration_limit"


class TestCancel:
    async def test_cancel_before_round_stops_cancelled(self) -> None:
        llm = _llm_rounds([_final_response("never reached")])
        registry = _registry(execute=AsyncMock(return_value={"ok": True}))
        with pytest.raises(_LoopStopError) as exc_info:
            await _chat_with_tools(
                [{"role": "user", "content": "hi"}],
                "m",
                [],
                registry,
                None,
                None,
                model_service=llm,
                cancel_check=lambda: True,
            )
        assert exc_info.value.reason_category == "cancelled"
        assert llm.calls == 0

    async def test_async_cancel_check_supported(self) -> None:
        async def async_cancel() -> bool:
            return True

        llm = _llm_rounds([_final_response("never")])
        registry = _registry(execute=AsyncMock(return_value={"ok": True}))
        with pytest.raises(_LoopStopError) as exc_info:
            await _chat_with_tools(
                [{"role": "user", "content": "hi"}],
                "m",
                [],
                registry,
                None,
                None,
                model_service=llm,
                cancel_check=async_cancel,
            )
        assert exc_info.value.reason_category == "cancelled"


class TestReplayBeforeDispatch:
    async def test_replayed_outcome_returns_without_dispatch(self) -> None:
        # Restart re-entry: the receipt is already decided; the loop replays
        # the real result and never re-dispatches the business call.
        replayed_payload = {"ticket_id": "TKT-77"}
        registry = _registry(
            execute=AsyncMock(return_value={"should": "not-happen"}),
            completed_result=AsyncMock(return_value=(True, replayed_payload)),
        )
        results = await _execute_tool_calls(
            registry,
            [dict(TOOL_CALL)],
            "session-1",
        )
        assert results[0]["result"] == replayed_payload
        assert results[0]["is_error"] is False
        registry.execute.assert_not_awaited()

    async def test_undecided_outcome_dispatches_normally(self) -> None:
        registry = _registry(execute=AsyncMock(return_value={"ticket_id": "TKT-1"}))
        results = await _execute_tool_calls(
            registry,
            [dict(TOOL_CALL)],
            "session-2",
        )
        assert results[0]["is_error"] is False
        registry.execute.assert_awaited_once()


class TestReplayIdentity:
    """step5e task 3.1-3.3 — the logical-call identity is stable across
    restart/re-entry and derives from session + turn + tool + args digest."""

    def _loop(self, registry, history: list[dict[str, Any]] | None = None, rounds=None):
        llm = _llm_rounds(rounds or [_tool_call_response(TOOL_CALL), _final_response("done")])
        captured: list[list] = []

        original_chat = llm.chat

        async def chat(**kwargs: Any) -> Any:
            captured.append(list(kwargs["messages"]))
            return await original_chat(**kwargs)

        llm.chat = chat
        return llm, captured

    async def test_identity_stable_across_reentry(self) -> None:
        # Entry 1: dispatch round 0 with arguments D; the governed registry
        # records the claim under chat:{session}:0:{tool}:{digest}.
        # Entry 2 (restart): the same history + same round derives the SAME
        # identity — the registry's completed_result sees a stable key.
        seen_keys: list[str] = []

        async def execute(name, args, context=None):
            seen_keys.append(context["invocation_id"])
            return {"ok": True}

        openai_shaped = {
            "id": "call_1",
            "type": "function",
            "function": {"name": "inventory_write", "arguments": '{"sku": "42"}'},
        }
        history = [{"role": "user", "content": "write it"}]
        for _ in range(2):
            registry = _registry(execute=execute)
            llm = _llm_rounds([_tool_call_response(openai_shaped), _final_response("done")])
            await _chat_with_tools(
                list(history),
                "m",
                [],
                registry,
                None,
                None,
                model_service=llm,
                session_id="sess-9",
                max_iterations=2,
            )
        assert len(seen_keys) == 2
        assert seen_keys[0] == seen_keys[1]
        assert seen_keys[0].startswith("chat:sess-9:0:inventory_write:")

    async def test_distinct_arguments_distinct_identity(self) -> None:
        seen_keys: list[str] = []

        async def execute(name, args, context=None):
            seen_keys.append(context["invocation_id"])
            return {"ok": True}

        registry = _registry(execute=execute)
        llm = _llm_rounds(
            [
                _tool_call_response(
                    {**TOOL_CALL, "function": {"name": "inventory_write", "arguments": '{"sku": "1"}'}}
                ),
                _tool_call_response(
                    {**TOOL_CALL, "function": {"name": "inventory_write", "arguments": '{"sku": "2"}'}}
                ),
                _final_response("done"),
            ]
        )
        await _chat_with_tools(
            [{"role": "user", "content": "write both"}],
            "m",
            [],
            registry,
            None,
            None,
            model_service=llm,
            session_id="sess-a",
            max_iterations=3,
        )
        assert len(seen_keys) == 2
        assert seen_keys[0] != seen_keys[1]

    async def test_history_anchored_turn_base(self) -> None:
        # Re-entry with prior tool turns in history: the round numbering
        # continues from the history (turn_base), not from zero.
        seen_keys: list[str] = []

        async def execute(name, args, context=None):
            seen_keys.append(context["invocation_id"])
            return {"ok": True}

        history = [
            {"role": "user", "content": "write it"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "old", "type": "function", "function": {"name": "inventory_write", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "tool_call_id": "old", "content": "ok"},
        ]
        registry = _registry(execute=execute)
        openai_shaped = {
            "id": "call_1",
            "type": "function",
            "function": {"name": "inventory_write", "arguments": '{"sku": "42"}'},
        }
        llm = _llm_rounds([_tool_call_response(openai_shaped), _final_response("done")])
        await _chat_with_tools(
            list(history),
            "m",
            [],
            registry,
            None,
            None,
            model_service=llm,
            session_id="sess-b",
            max_iterations=2,
        )
        assert seen_keys and seen_keys[0].startswith("chat:sess-b:1:inventory_write:")


class TestStructuredOutput:
    """step5e task 4.1-4.2 — declared output schema: validate before return,
    exactly one feedback retry, invalid_output failure with error summary."""

    SCHEMA = {
        "type": "object",
        "required": ["answer"],
        "properties": {"answer": {"type": "string"}},
    }

    async def _run(self, rounds, output_schema):
        llm = _llm_rounds(rounds)
        registry = _registry(execute=AsyncMock(return_value={"ok": True}))
        result = await _chat_with_tools(
            [{"role": "user", "content": "hi"}],
            "m",
            [],
            registry,
            None,
            None,
            model_service=llm,
            output_schema=output_schema,
        )
        return result, llm

    async def test_valid_output_passes_without_retry(self) -> None:
        import json

        good = _final_response(json.dumps({"answer": "42"}))
        result, llm = await self._run([good], self.SCHEMA)
        assert result.content == json.dumps({"answer": "42"})
        assert llm.calls == 1

    async def test_invalid_then_valid_uses_exactly_one_retry(self) -> None:
        import json

        bad = _final_response("not json")
        good = _final_response(json.dumps({"answer": "42"}))
        result, llm = await self._run([bad, good], self.SCHEMA)
        assert json.loads(result.content) == {"answer": "42"}
        assert llm.calls == 2  # exactly one bounded retry

    async def test_invalid_after_retry_fails_invalid_output(self) -> None:
        bad1 = _final_response("nope")
        bad2 = _final_response("still nope")
        with pytest.raises(_LoopStopError) as exc_info:
            await self._run([bad1, bad2], self.SCHEMA)
        assert exc_info.value.reason_category == "invalid_output"
        assert "violates the declared output schema" in exc_info.value.detail

    async def test_no_schema_unchanged(self) -> None:
        weird = _final_response("plain text, not json")
        result, llm = await self._run([weird], None)
        assert result.content == "plain text, not json"
        assert llm.calls == 1


async def test_unresolved_replay_stops_before_any_dispatch():
    registry = _registry(
        execute=AsyncMock(),
        completed_result=AsyncMock(side_effect=OutcomeUnknownError("pending claim")),
    )
    with pytest.raises(_LoopStopError, match="needs_review"):
        await _execute_tool_calls(registry, [dict(TOOL_CALL)], "session")
    registry.execute.assert_not_awaited()


async def test_output_retry_budget_is_terminal():
    from hecate.core.composition.governance import BudgetRefusedError

    llm = MagicMock()
    llm.chat = AsyncMock(side_effect=[_final_response("bad"), BudgetRefusedError("exhausted")])
    with pytest.raises(_LoopStopError, match="budget_exhausted"):
        await _chat_with_tools(
            [], "m", [], _registry(), None, None, model_service=llm, output_schema={"type": "object"}
        )
    assert llm.chat.await_count == 2
    assert llm.chat.call_args.kwargs["tools"] is None


async def test_output_retry_checks_cancellation():
    checks = iter([False, True])
    llm = _llm_rounds([_final_response("bad"), _final_response("{}")])
    with pytest.raises(_LoopStopError, match="cancelled"):
        await _chat_with_tools(
            [],
            "m",
            [],
            _registry(),
            None,
            None,
            model_service=llm,
            cancel_check=lambda: next(checks),
            output_schema={"type": "object"},
        )
    assert llm.calls == 1


async def test_output_repair_never_dispatches_tools():
    llm = _llm_rounds([_final_response("bad"), _tool_call_response(TOOL_CALL)])
    registry = _registry(execute=AsyncMock())
    with pytest.raises(_LoopStopError, match="invalid_output"):
        await _chat_with_tools([], "m", [], registry, None, None, model_service=llm, output_schema={"type": "object"})
    registry.execute.assert_not_awaited()


async def test_trimmed_history_never_replays_previous_turn():
    from hecate.channel.api.v1.chat import _replay_invocation_id

    receipts = {}
    keys = []

    async def execute(name, args, context):
        key = context["invocation_id"]
        keys.append(key)
        receipts[key] = {"receipt": len(keys)}
        return receipts[key]

    async def replay(name, args, context):
        key = context["invocation_id"]
        return key in receipts, receipts.get(key)

    registry = _registry(execute=execute, completed_result=replay)
    call = {"id": "fresh", "type": "function", "function": {"name": "inventory_write", "arguments": "{}"}}
    for history in ([{"role": "user", "content": "old request"}], [{"role": "user", "content": "new request"}]):
        await _chat_with_tools(
            history,
            "m",
            [],
            registry,
            None,
            None,
            session_id="sess",
            model_service=_llm_rounds([_tool_call_response(call), _final_response("done")]),
        )
    assert len(keys) == 2 and keys[0] != keys[1]
    assert _replay_invocation_id("sess", 0, "tool", {}) != _replay_invocation_id("sess", 1, "tool", {})


async def test_legacy_receipt_is_read_before_new_dispatch():
    calls = []

    async def replay(name, args, context):
        calls.append(context["invocation_id"])
        return context["invocation_id"] == "chat:sess:call_1", {"old": "result"}

    registry = _registry(execute=AsyncMock(), completed_result=replay)
    result = await _execute_tool_calls(registry, [dict(TOOL_CALL)], "sess")
    assert result[0]["result"] == {"old": "result"}
    assert len(calls) == 2
    registry.execute.assert_not_awaited()


async def test_persisted_pending_turn_replays_receipt_before_model_resumes():
    from hecate.channel.api.v1.chat import _history_ref

    receipts = {}
    executions = []

    async def execute(name, args, context):
        executions.append(name)
        receipts[context["invocation_id"]] = {"ticket": "real-TKT"}
        return receipts[context["invocation_id"]]

    async def replay(name, args, context):
        key = context["invocation_id"]
        return key in receipts, receipts.get(key)

    registry = _registry(execute=execute, completed_result=replay)
    prefix = [{"role": "user", "content": "write ticket"}]
    await _execute_tool_calls(registry, [dict(TOOL_CALL)], "session", history_ref=_history_ref(prefix))
    openai_call = {
        "id": TOOL_CALL["id"],
        "type": "function",
        "function": {
            "name": TOOL_CALL["name"],
            "arguments": __import__("json").dumps(TOOL_CALL["arguments"]),
        },
    }
    history = prefix + [{"role": "assistant", "content": "", "tool_calls": [openai_call]}]
    captured = []

    async def chat(**kwargs):
        captured.extend(kwargs["messages"])
        return _final_response("done")

    result = await _chat_with_tools(
        history,
        "m",
        [],
        registry,
        None,
        None,
        session_id="session",
        model_service=type("L", (), {"chat": staticmethod(chat)})(),
    )
    assert result.content == "done"
    assert executions == [TOOL_CALL["name"]]
    assert captured[-1]["role"] == "tool" and "real-TKT" in captured[-1]["content"]
