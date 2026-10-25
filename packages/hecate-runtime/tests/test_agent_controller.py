"""Multi-turn controller loop over the accepted-turn stack (deterministic).

Covers the controller slice: the automatic decision→action→feedback→next
decision loop without any graph, continuation expressed only through the
durable accepted-decision chain (deterministic step ids and frozen
references), turn/deadline/cancel gates that survive re-entry, stop
classification, and wait signals propagating untouched to the host. Real
SQL stores, leases and cross-process restarts live in the hecate-durable
integration suite.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from hecate_runtime.accepted_action_step import execution_id_for_action
from hecate_runtime.accepted_decision import (
    AcceptedAction,
    DecisionConflictError,
    DecisionValidationError,
)
from hecate_runtime.agent_controller import (
    CONTROLLER_LOOP_MARKER,
    AgentController,
    ControllerExecutionDriver,
    ControllerLimits,
    ControllerRunSpec,
    PortTurnResponderSource,
    TurnResponderSource,
    decision_step_id_for_turn,
)
from hecate_runtime.agent_turn import AgentTurn
from hecate_runtime.decision_step import KIND_FINAL_ANSWER, DecisionProposal, DecisionResponder
from hecate_runtime.errors import ApprovalWaitSignal
from hecate_runtime.eventstore import InMemoryEventStore
from hecate_runtime.execution_service import (
    RuntimeExecutionRequest,
    RuntimeExecutionState,
    runtime_execution_service,
)
from hecate_runtime.tool_call_executor import ToolCallExecutor
from test_accepted_action_step import STUB_OWNER, StubLedger, StubToolPort, _run_ref
from test_decision_step import InMemoryDecisionStore

# The executor formats dict tool results with str() on first run, while the
# ledger's replayed content is JSON — the projection preserves whichever
# governed content is present, so scripts accept both serializations.
_RESULT_OK = str({"ok": True, "by": "memory_add"})
_RESULT_OK_REPLAY = json.dumps({"ok": True, "by": "memory_add"})
_RESULT_SEARCH = str({"ok": True, "by": "web_search"})
_RESULT_SEARCH_REPLAY = json.dumps({"ok": True, "by": "web_search"})


def _loop_key(messages: list[dict[str, Any]]) -> tuple[int, str]:
    """Script key: how many governed results the loop already consumed."""
    tool_messages = [message for message in messages if message.get("role") == "tool"]
    tail = tool_messages[-1]["content"] if tool_messages else ""
    return len(tool_messages), str(tail)


class LoopScriptedSource(TurnResponderSource):
    """Script the loop by position in the projected conversation.

    A fresh turn for an unscripted position is forbidden, so the script
    doubles as an assertion that the loop only continues from projected
    facts and never re-decides a position twice.
    """

    def __init__(self, script: dict[tuple[int, str], DecisionProposal], *, frozen: dict[str, Any] | None = None):
        self.script = script
        self._frozen = frozen or {"config": "v1"}
        self.turns: list[list[dict[str, Any]]] = []
        # responder_for builds a frozen responder for EVERY turn (including
        # replays); model_asks counts the responders that were actually asked.
        self.model_asks: list[str] = []

    def frozen_refs(self) -> dict[str, Any]:
        return dict(self._frozen)

    def responder_for(self, messages: list[dict[str, Any]], input_refs: dict[str, Any]) -> DecisionResponder:
        self.turns.append(json.loads(json.dumps(messages)))
        key = _loop_key(messages)
        proposal = self.script.get(key)
        source = self

        class _OneShot(DecisionResponder):
            def __init__(self) -> None:
                self.calls = 0

            async def respond(self, run_ref, decision_step_id, refs) -> DecisionProposal:
                self.calls += 1
                if proposal is None:
                    raise AssertionError(f"scripted loop decided an unsolicited turn at {key!r}")
                source.model_asks.append(decision_step_id)
                return proposal

        return _OneShot()


def _actions(*items: tuple[str, str, dict[str, Any]]) -> DecisionProposal:
    return DecisionProposal(
        accepted_output={"text": "working"},
        actions=tuple(AcceptedAction(action_key=key, tool=tool, arguments=args) for key, tool, args in items),
    )


def _final(text: str) -> DecisionProposal:
    return DecisionProposal(accepted_output={"text": text}, actions=(), kind=KIND_FINAL_ANSWER)


def _controller(
    store: InMemoryDecisionStore,
    source: TurnResponderSource,
    port: StubToolPort,
    ledger: StubLedger | None = None,
) -> AgentController:
    from test_decision_step import ScriptedResponder

    # The constructor responder is the loop's canary: the controller must
    # supply per-turn responders, so this one must never be asked. Sharing
    # the ledger across controller instances mirrors the durable store: the
    # SQL suite owns real cross-process persistence.
    executor = ToolCallExecutor(port=port, event_store=InMemoryEventStore(), action_hook=ledger or StubLedger())
    return AgentController(AgentTurn(store, ScriptedResponder(), executor), source)


def _spec(run_ref=None, **kwargs) -> ControllerRunSpec:
    return ControllerRunSpec(
        run_ref=run_ref or _run_ref(),
        base_messages=[{"role": "user", "content": "check the stock"}],
        ownership=STUB_OWNER,
        **kwargs,
    )


async def test_loop_advances_on_real_tool_feedback_until_final_answer() -> None:
    """Turn 2 must depend on turn 1's real result; nobody drives the turns."""
    store, port = InMemoryDecisionStore(), StubToolPort()
    source = LoopScriptedSource(
        {
            (0, ""): _actions(("a1", "memory_add", {"q": 1})),
            (1, _RESULT_OK): _actions(("a2", "web_search", {"q": 2})),
            (2, _RESULT_SEARCH): _final("the answer"),
        }
    )
    outcome = await _controller(store, source, port).run(_spec())
    assert outcome.is_final_answer
    assert outcome.final_text == "the answer"
    assert outcome.turns == 3
    assert outcome.decision_step_ids == (
        decision_step_id_for_turn("agent-turn", 1),
        decision_step_id_for_turn("agent-turn", 2),
        decision_step_id_for_turn("agent-turn", 3),
    )
    # The second decision consumed the first turn's real governed result.
    tool_tails = [message for message in source.turns[1] if message.get("role") == "tool"]
    assert tool_tails[0]["content"] == _RESULT_OK
    assert [(name, args) for name, args, _ in port.calls] == [("memory_add", {"q": 1}), ("web_search", {"q": 2})]


async def test_reentry_at_same_budget_replays_chain_with_zero_fresh_work() -> None:
    """A second controller instance re-derives the same chain from storage."""
    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    source = LoopScriptedSource(
        {
            (0, ""): _actions(("a1", "memory_add", {"q": 1})),
            (1, _RESULT_OK): _actions(("a2", "web_search", {"q": 2})),
            (2, _RESULT_SEARCH): _final("the answer"),
        }
    )
    run_ref = _run_ref()
    first = await _controller(store, source, port, ledger).run(_spec(run_ref, limits=ControllerLimits(max_turns=2)))
    assert first.state == "stopped" and first.reason == "turn_limit"
    assert len(port.calls) == 2

    fresh_port = StubToolPort()
    recovery = LoopScriptedSource({})  # any fresh decision attempt must fail the test
    # The durable turn budget is not reset by the restart.
    second = await _controller(store, recovery, fresh_port, ledger).run(
        _spec(run_ref, limits=ControllerLimits(max_turns=2))
    )
    assert second.state == "stopped" and second.reason == "turn_limit"
    assert recovery.model_asks == []  # committed turns replay with zero model calls
    assert fresh_port.calls == []  # completed actions replay with zero business calls
    assert second.decision_step_ids == first.decision_step_ids


async def test_reentry_with_higher_budget_continues_the_chain() -> None:
    """Same chain, a raised budget: replay turns 1-2, then decide turn 3."""
    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    script = {
        (0, ""): _actions(("a1", "memory_add", {"q": 1})),
        (1, _RESULT_OK): _actions(("a2", "web_search", {"q": 2})),
        (2, _RESULT_SEARCH): _final("continued"),
    }
    run_ref = _run_ref()
    first = await _controller(store, LoopScriptedSource(script), port, ledger).run(
        _spec(run_ref, limits=ControllerLimits(max_turns=2))
    )
    assert first.reason == "turn_limit"

    fresh_port = StubToolPort()
    # Replayed turns re-serialize their governed results from the ledger.
    second_source = LoopScriptedSource(
        {
            (0, ""): _actions(("a1", "memory_add", {"q": 1})),
            (1, _RESULT_OK_REPLAY): _actions(("a2", "web_search", {"q": 2})),
            (2, _RESULT_SEARCH_REPLAY): _final("continued"),
        }
    )
    second = await _controller(store, second_source, fresh_port, ledger).run(_spec(run_ref))
    assert second.is_final_answer and second.final_text == "continued"
    assert second.turns == 3
    # Only turn 3 was freshly decided; the earlier turns replayed.
    assert second_source.model_asks == [decision_step_id_for_turn("agent-turn", 3)]
    assert fresh_port.calls == []


async def test_frozen_reference_drift_conflicts_on_replay() -> None:
    """A different frozen config re-presenting turn 1 is an explicit conflict."""
    store, port = InMemoryDecisionStore(), StubToolPort()
    run_ref = _run_ref()
    script = {(0, ""): _actions(("a1", "memory_add", {"q": 1}))}
    await _controller(store, LoopScriptedSource(script), port).run(_spec(run_ref, limits=ControllerLimits(max_turns=1)))
    drifted = LoopScriptedSource(script, frozen={"config": "v2"})
    with pytest.raises(DecisionConflictError):
        await _controller(store, drifted, StubToolPort()).run(_spec(run_ref, limits=ControllerLimits(max_turns=1)))


async def test_failed_action_turn_stops_with_classified_reason() -> None:
    store = InMemoryDecisionStore()
    failing_port = StubToolPort(error=RuntimeError("business refused"))
    outcome = await _controller(
        store, LoopScriptedSource({(0, ""): _actions(("a1", "memory_add", {"q": 1}))}), failing_port
    ).run(_spec())
    assert outcome.state == "stopped"
    assert outcome.reason == "action_failed"
    assert outcome.turns == 1


async def test_cancel_and_deadline_gates_stop_before_new_turns() -> None:
    store, port = InMemoryDecisionStore(), StubToolPort()
    source = LoopScriptedSource({(0, ""): _final("never asked")})
    cancelled = await _controller(store, source, port).run(_spec(), should_cancel=lambda: True)
    assert cancelled.state == "stopped" and cancelled.reason == "cancel_requested"
    assert cancelled.turns == 0
    assert source.turns == []

    def late_clock() -> datetime:
        return datetime.now(UTC) + timedelta(hours=1)

    past_deadline = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    expired = await _controller(store, source, port).run(
        _spec(clock=late_clock, limits=ControllerLimits(deadline=past_deadline))
    )
    assert expired.reason == "deadline_exceeded"


async def test_deadline_gate_survives_into_committed_chain_replay() -> None:
    """An elapsed deadline also stops a replay before pending work re-dispatches."""
    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    deadline = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    await _controller(store, LoopScriptedSource({(0, ""): _actions(("a1", "memory_add", {"q": 1}))}), port, ledger).run(
        _spec(run_ref, limits=ControllerLimits(max_turns=1, deadline=deadline))
    )
    # The original deadline has since elapsed while the run was interrupted.
    elapsed = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    fresh_port = StubToolPort()
    replay = await _controller(store, LoopScriptedSource({}), fresh_port, ledger).run(
        _spec(run_ref, limits=ControllerLimits(max_turns=4, deadline=elapsed))
    )
    assert replay.reason == "deadline_exceeded"
    assert fresh_port.calls == []


async def test_approval_wait_signal_escapes_the_loop_untouched() -> None:
    """A durable wait parks the host; committed turns stay; the wake continues."""
    from hecate_runtime.tool_access import AccessDecision, ApprovalDecision

    store, ledger = InMemoryDecisionStore(), StubLedger()

    class WaitingApproval:
        """Raise the durable-wait signal once; approve after the wake."""

        def __init__(self) -> None:
            self.calls = 0

        async def request_approval(self, tool_name, arguments, risk_level, context):
            self.calls += 1
            if self.calls == 1:
                raise ApprovalWaitSignal(tool_name, arguments)
            return ApprovalDecision(approved=True, reason="wake grant", approval_ref="approval-1")

    class WaitingPolicy:
        """The q=2 action requires approval; everything else is allowed."""

        def __init__(self) -> None:
            self.calls = 0

        def evaluate(self, tool_meta, rules, eval_context, arguments=None):
            self.calls += 1
            return AccessDecision.REQUIRE_APPROVAL if arguments.get("q") == 2 else AccessDecision.EXECUTE

    waiting_port, waiting_approval, waiting_policy = StubToolPort(), WaitingApproval(), WaitingPolicy()
    run_ref = _run_ref()
    script = {
        (0, ""): _actions(("a1", "memory_add", {"q": 1})),
        (1, _RESULT_OK): _actions(("a2", "memory_add", {"q": 2})),
        (2, _RESULT_OK): _final("done"),
    }
    executor = ToolCallExecutor(
        port=waiting_port,
        event_store=InMemoryEventStore(),
        action_hook=ledger,
        access_policy=waiting_policy,
        approval_callback=waiting_approval,
    )
    from test_decision_step import ScriptedResponder

    waiting_controller = AgentController(AgentTurn(store, ScriptedResponder(), executor), LoopScriptedSource(script))
    with pytest.raises(ApprovalWaitSignal):
        await waiting_controller.run(_spec(run_ref))
    assert [(name, args["q"]) for name, args, _ in waiting_port.calls] == [("memory_add", 1)]
    # The wait raised at the approval boundary — before any ledger claim —
    # so the parked action has no dangling claim to reconcile.
    assert len(ledger.claims) == 1  # only turn 1's completed action was claimed

    # The wake: same run, approval granted — replay turn 1 (storage only),
    # re-decide nothing, dispatch the waiting action, then finish.
    woke_port, woke_policy = StubToolPort(), WaitingPolicy()
    woke_executor = ToolCallExecutor(
        port=woke_port,
        event_store=InMemoryEventStore(),
        action_hook=ledger,
        access_policy=woke_policy,
        approval_callback=waiting_approval,  # second ask approves: the wake grant
    )
    woke = LoopScriptedSource(
        {
            (0, ""): _actions(("a1", "memory_add", {"q": 1})),
            (1, _RESULT_OK_REPLAY): _actions(("a2", "memory_add", {"q": 2})),
            (2, _RESULT_OK): _final("done"),
        }
    )
    outcome = await AgentController(AgentTurn(store, ScriptedResponder(), woke_executor), woke).run(_spec(run_ref))
    assert outcome.is_final_answer and outcome.final_text == "done"
    assert woke.model_asks == [decision_step_id_for_turn("agent-turn", 3)]  # only turn 3 was fresh
    # Turn 1 replayed from the ledger; only the parked action dispatched.
    assert [(name, args["q"]) for name, args, _ in woke_port.calls] == [("memory_add", 2)]


async def test_input_refs_pin_loop_marker_turn_and_frozen_versions() -> None:
    store, port = InMemoryDecisionStore(), StubToolPort()
    seen: list[dict[str, Any]] = []

    class RecordingSource(LoopScriptedSource):
        def responder_for(self, messages, input_refs):
            seen.append(dict(input_refs))
            return super().responder_for(messages, input_refs)

    await _controller(store, RecordingSource({(0, ""): _final("done")}), port).run(_spec())
    assert seen[0]["controller"] == CONTROLLER_LOOP_MARKER
    assert seen[0]["turn"] == 1
    assert seen[0]["config"] == "v1"


async def test_driver_streams_events_and_retains_outcome() -> None:
    store, port = InMemoryDecisionStore(), StubToolPort()
    source = LoopScriptedSource(
        {
            (0, ""): _actions(("a1", "memory_add", {"q": 1})),
            (1, _RESULT_OK): _final("done"),
        }
    )
    controller = _controller(store, source, port)
    driver = ControllerExecutionDriver(controller, _spec())
    result = await runtime_execution_service.execute(
        RuntimeExecutionRequest(runtime=driver, session_id=uuid.uuid4(), initial_input={})
    )
    assert result.state is RuntimeExecutionState.SUCCEEDED
    types = [event["type"] for event in result.events]
    assert "controller/turn_started" in types and "controller/final_answer" in types
    assert driver.outcome is not None and driver.outcome.is_final_answer


async def test_driver_cooperative_cancel_stops_between_turns() -> None:
    store, port = InMemoryDecisionStore(), StubToolPort()
    source = LoopScriptedSource(
        {
            (0, ""): _actions(("a1", "memory_add", {"q": 1})),
            (1, _RESULT_OK): _final("never"),
        }
    )
    controller = _controller(store, source, port)
    driver = ControllerExecutionDriver(controller, _spec())
    seen = {"events": 0}

    def count_event(event: dict) -> str:
        seen["events"] += 1
        return "continue"

    result = await runtime_execution_service.execute(
        RuntimeExecutionRequest(
            runtime=driver,
            session_id=uuid.uuid4(),
            initial_input={},
            observer=count_event,
            should_cancel=lambda: seen["events"] >= 2,  # after turn 1's start+complete
        )
    )
    assert result.state is RuntimeExecutionState.CANCELLED
    # Turn 2 was never decided: its responder was not built.
    assert len(source.turns) == 1
    assert driver.outcome is None


def test_turn_responder_source_is_abstract() -> None:
    """The extension point cannot be instantiated without a real source."""
    with pytest.raises(TypeError):
        TurnResponderSource()  # type: ignore[abstract]


def test_decision_step_id_and_limits_validation() -> None:
    assert decision_step_id_for_turn("loop", 1) == "loop-000001"
    with pytest.raises(DecisionValidationError):
        decision_step_id_for_turn("bad prefix", 1)
    with pytest.raises(DecisionValidationError):
        decision_step_id_for_turn("loop", 0)
    with pytest.raises(DecisionValidationError):
        ControllerLimits(max_turns=0)
    with pytest.raises(DecisionValidationError):
        ControllerLimits(deadline="not-a-date")


def test_port_turn_responder_source_rejects_mismatched_tool_snapshot() -> None:
    from hecate_runtime.ports import StubRuntimePort

    with pytest.raises(DecisionValidationError):
        PortTurnResponderSource(
            StubRuntimePort(),
            config={"model": "m"},
            tool_definitions={"search": {"type": "function", "function": {"name": "other"}}},
            base_refs={"config": "v1"},
        )


def test_execution_id_stability_for_loop_actions() -> None:
    """Loop turn actions keep the stable accepted identity across replays."""
    run_ref = _run_ref()
    first = execution_id_for_action(run_ref, decision_step_id_for_turn("t", 1), "a1")
    second = execution_id_for_action(run_ref, decision_step_id_for_turn("t", 2), "a1")
    assert first != second  # per-turn scoping
    assert first == execution_id_for_action(run_ref, decision_step_id_for_turn("t", 1), "a1")


async def test_changed_base_input_cannot_resume_an_accepted_chain() -> None:
    """Input drift must conflict before a new decision or business dispatch."""
    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    await _controller(store, LoopScriptedSource({(0, ""): _final("original")}), port, ledger).run(_spec(run_ref))
    changed = _spec(run_ref)
    changed.base_messages[0]["content"] = "a different task"
    source = LoopScriptedSource({})
    with pytest.raises(DecisionConflictError):
        await _controller(store, source, port, ledger).run(changed)
    assert source.model_asks == []
    assert port.calls == []


async def test_port_source_freezes_content_and_pins_actual_snapshots() -> None:
    """Mutable caller objects cannot replace configuration under the same refs."""
    from hecate_runtime.ports import StubRuntimePort

    class RecordingPort(StubRuntimePort):
        """Capture the actual configuration sent to the model."""

        async def llm_invoke_structured(self, messages, config):
            self.config = config
            yield {"content": "done", "tool_calls": None}

    port = RecordingPort()
    config = {"model": "original", "temperature": 0}
    tools: dict[str, Any] = {
        "search": {"type": "function", "function": {"name": "search", "parameters": {"type": "object"}}}
    }
    refs = {"config": "v1"}
    source = PortTurnResponderSource(port, config=config, tool_definitions=tools, base_refs=refs)
    original_refs = source.frozen_refs()
    config["model"] = "replacement"
    tools["search"]["function"]["parameters"]["type"] = "string"
    refs["config"] = "v2"
    assert source.frozen_refs() == original_refs
    responder = source.responder_for([], original_refs)
    await responder.respond(_run_ref(), "turn-1", original_refs)
    assert port.config["model"] == "original"
    assert port.config["tools"][0]["function"]["parameters"]["type"] == "object"
    changed = PortTurnResponderSource(port, config=config, tool_definitions=tools, base_refs={"config": "v1"})
    assert changed.frozen_refs() != original_refs


@pytest.mark.parametrize("stop_during", ["model", "first_action"])
async def test_deadline_is_rechecked_after_model_and_between_actions(stop_during) -> None:
    """A slow model or earlier action cannot dispatch work past the deadline."""
    late = [False]
    now = datetime.now(UTC)

    class AdvancingPort(StubToolPort):
        """Move the clock after the first confirmed business action."""

        async def tool_execute(self, name, args, context=None):
            result = await super().tool_execute(name, args, context)
            late[0] = True
            return result

    class AdvancingSource(LoopScriptedSource):
        """A model response can itself take the task past its deadline."""

        def responder_for(self, messages, input_refs):
            inner = super().responder_for(messages, input_refs)

            class AdvancingResponder(DecisionResponder):
                """Return the proposal after simulating slow model work."""

                async def respond(self, run_ref, decision_step_id, input_refs):
                    proposal = await inner.respond(run_ref, decision_step_id, input_refs)
                    if stop_during == "model":
                        late[0] = True
                    return proposal

            return AdvancingResponder()

    port = AdvancingPort()
    source = AdvancingSource({(0, ""): _actions(("a1", "memory_add", {"q": 1}), ("a2", "memory_add", {"q": 2}))})
    outcome = await _controller(InMemoryDecisionStore(), source, port).run(
        _spec(
            limits=ControllerLimits(deadline=(now + timedelta(hours=1)).isoformat()),
            clock=lambda: now + timedelta(hours=2) if late[0] else now,
        )
    )
    assert outcome.reason == "deadline_exceeded"
    assert len(port.calls) == (0 if stop_during == "model" else 1)


async def test_driver_cancel_callback_stops_remaining_actions_in_a_turn() -> None:
    """The driver must pass host cancellation into the batch boundary."""
    cancelled = [False]

    class CancellingPort(StubToolPort):
        """Simulate a cancel arriving while the first tool is running."""

        async def tool_execute(self, name, args, context=None):
            result = await super().tool_execute(name, args, context)
            cancelled[0] = True
            return result

    port = CancellingPort()
    source = LoopScriptedSource({(0, ""): _actions(("a1", "memory_add", {}), ("a2", "memory_add", {}))})
    driver = ControllerExecutionDriver(
        _controller(InMemoryDecisionStore(), source, port), _spec(), should_cancel=lambda: cancelled[0]
    )
    result = await runtime_execution_service.execute(
        RuntimeExecutionRequest(runtime=driver, session_id=uuid.uuid4(), initial_input={})
    )
    assert result.succeeded
    assert driver.outcome is not None and driver.outcome.reason == "cancel_requested"
    assert len(port.calls) == 1
