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

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from hecate_runtime.accepted_action_step import execution_id_for_action
from hecate_runtime.accepted_decision import (
    AcceptedAction,
    DecisionConflictError,
    DecisionStoreUnavailableError,
    DecisionValidationError,
)
from hecate_runtime.agent_controller import (
    CONTROLLER_LOOP_MARKER,
    AgentController,
    ControllerExecutionDriver,
    ControllerLimits,
    ControllerRunSpec,
    NoProgressPolicy,
    PortTurnResponderSource,
    TurnResponderSource,
    decision_step_id_for_turn,
)
from hecate_runtime.agent_harness import HarnessSection, HarnessSpec
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


def _spec(run_ref=None, base_messages=None, **kwargs) -> ControllerRunSpec:
    return ControllerRunSpec(
        run_ref=run_ref or _run_ref(),
        base_messages=base_messages if base_messages is not None else [{"role": "user", "content": "check the stock"}],
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


def _provided_positions(messages: list[dict[str, Any]]) -> list[int]:
    """Indexes of injected input-revision user messages, in order."""
    return [
        index
        for index, message in enumerate(messages)
        if message.get("role") == "user" and "provided_input" in str(message.get("content"))
    ]


def _tool_positions(messages: list[dict[str, Any]]) -> list[int]:
    return [index for index, message in enumerate(messages) if message.get("role") == "tool"]


class _RecordingSource(LoopScriptedSource):
    """Capture each turn's rebuilt messages and the refs it pins."""

    def __init__(self, script):
        super().__init__(script)
        self.seen: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []

    def responder_for(self, messages, input_refs):
        self.seen.append((json.loads(json.dumps(messages)), dict(input_refs)))
        return super().responder_for(messages, input_refs)


async def test_input_revisions_fold_at_causal_boundaries_and_pin_visible_ids() -> None:
    """A revision enters only at its boundary; decisions pin what they saw."""
    from hecate_runtime.agent_controller import ControllerInputRevision

    store, port = InMemoryDecisionStore(), StubToolPort()
    source = _RecordingSource(
        {
            (0, ""): _actions(("a1", "memory_add", {"q": 1})),
            (1, _RESULT_OK): _actions(("a2", "web_search", {"q": 2})),
            (2, _RESULT_SEARCH): _final("the answer"),
        }
    )
    revisions = (
        ControllerInputRevision("rev-early", {"region": "EU"}, 1),
        ControllerInputRevision("rev-late", {"tier": "gold"}, 3),
    )
    outcome = await _controller(store, source, port).run(_spec(input_revisions=revisions))
    assert outcome.is_final_answer

    turn1_messages, turn1_refs = source.seen[0]
    assert turn1_refs["inputs"] == ["rev-early"]
    assert _provided_positions(turn1_messages) == [1]  # base + [rev-early]
    assert json.loads(turn1_messages[1]["content"]) == {"provided_input": {"region": "EU"}}

    turn2_messages, turn2_refs = source.seen[1]
    assert turn2_refs["inputs"] == ["rev-early"]
    assert _provided_positions(turn2_messages) == [1]  # base + [rev], then proj(1)
    assert _tool_positions(turn2_messages) == [3]

    turn3_messages, turn3_refs = source.seen[2]
    assert turn3_refs["inputs"] == ["rev-early", "rev-late"]
    # Causal order: rev-early(1) < tool-1(3) < tool-2(5) < rev-late(6).
    # The revision that arrived later sits after the earlier turns'
    # results, never before them.
    provided, tools = _provided_positions(turn3_messages), _tool_positions(turn3_messages)
    assert provided == [1, 6] and tools == [3, 5]
    assert json.loads(turn3_messages[6]["content"]) == {"provided_input": {"tier": "gold"}}


async def test_no_visible_revision_leaves_refs_without_inputs_key() -> None:
    """Chains that see no revision keep the pre-revision refs shape."""
    store, port = InMemoryDecisionStore(), StubToolPort()
    source = _RecordingSource({(0, ""): _final("done")})
    await _controller(store, source, port).run(_spec())
    assert "inputs" not in source.seen[0][1]


async def test_late_revision_continues_committed_chain_without_ref_conflict() -> None:
    """Turns committed before a revision replay unchanged; later turns bind it."""
    from hecate_runtime.agent_controller import ControllerInputRevision

    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    script = {
        (0, ""): _actions(("a1", "memory_add", {"q": 1})),
        (1, _RESULT_OK): _actions(("a2", "web_search", {"q": 2})),
    }
    first = await _controller(store, LoopScriptedSource(script), port, ledger).run(
        _spec(run_ref, limits=ControllerLimits(max_turns=2))
    )
    assert first.reason == "turn_limit"

    fresh_port = StubToolPort()
    second = _RecordingSource(
        {
            (0, ""): _actions(("a1", "memory_add", {"q": 1})),
            (1, _RESULT_OK_REPLAY): _actions(("a2", "web_search", {"q": 2})),
            (2, _RESULT_SEARCH_REPLAY): _final("continued with input"),
        }
    )
    late = (ControllerInputRevision("rev-after-park", {"region": "EU"}, 3),)
    outcome = await _controller(store, second, fresh_port, ledger).run(_spec(run_ref, input_revisions=late))
    assert outcome.is_final_answer and outcome.final_text == "continued with input"
    assert second.model_asks == [decision_step_id_for_turn("agent-turn", 3)]
    assert fresh_port.calls == []
    # Committed turns replay with their original (input-free) refs; the
    # fresh turn pins the revision it consumes.
    assert "inputs" not in second.seen[0][1]
    assert "inputs" not in second.seen[1][1]
    assert second.seen[2][1]["inputs"] == ["rev-after-park"]
    provided, tools = _provided_positions(second.seen[2][0]), _tool_positions(second.seen[2][0])
    assert tools[1] < provided[0]  # the input sits after the earlier turns


def test_input_revision_validation_rejects_unstable_identities() -> None:
    from hecate_runtime.agent_controller import ControllerInputRevision

    with pytest.raises(DecisionValidationError):
        ControllerInputRevision(" ", {"a": 1}, 1)
    with pytest.raises(DecisionValidationError):
        ControllerInputRevision("rev-1", "not-a-dict", 1)
    with pytest.raises(DecisionValidationError):
        ControllerInputRevision("rev-1", {"a": 1}, 0)
    with pytest.raises(DecisionValidationError):
        ControllerInputRevision("rev-1", {"a": 1}, "2")

    async def scenario(revisions) -> None:
        store, port = InMemoryDecisionStore(), StubToolPort()
        await _controller(store, LoopScriptedSource({(0, ""): _final("x")}), port).run(_spec(input_revisions=revisions))

    import asyncio

    duplicate = (
        ControllerInputRevision("rev-1", {"a": 1}, 1),
        ControllerInputRevision("rev-1", {"b": 2}, 2),
    )
    with pytest.raises(DecisionValidationError):
        asyncio.run(scenario(duplicate))


async def test_same_revision_identity_cannot_rewrite_consumed_content() -> None:
    """An unchanged id cannot conceal a different payload on recovery."""
    from hecate_runtime.agent_controller import ControllerInputRevision

    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    first = (ControllerInputRevision("rev-fixed", {"region": "EU"}, 1),)
    await _controller(store, LoopScriptedSource({(0, ""): _final("original")}), port, ledger).run(
        _spec(run_ref, input_revisions=first)
    )
    changed = (ControllerInputRevision("rev-fixed", {"region": "US"}, 1),)
    with pytest.raises(DecisionConflictError):
        await _controller(store, LoopScriptedSource({}), port, ledger).run(_spec(run_ref, input_revisions=changed))
    assert port.calls == []


def _harness_for(base_messages: list[dict[str, Any]]) -> HarnessSpec:
    """A minimal declared composition that renders exactly ``base_messages``."""
    return HarnessSpec(1, (HarnessSection("task", base_messages),))


async def test_harness_binding_pins_version_and_digest_on_every_decision() -> None:
    """A declared composition rides every turn's input_refs, verifiable per decision."""
    store, port = InMemoryDecisionStore(), StubToolPort()
    seen: list[dict[str, Any]] = []

    class RecordingSource(LoopScriptedSource):
        def responder_for(self, messages, input_refs):
            seen.append(dict(input_refs))
            return super().responder_for(messages, input_refs)

    base = [{"role": "user", "content": "check the stock"}]
    harness = _harness_for(base)
    await _controller(store, RecordingSource({(0, ""): _final("done")}), port).run(
        _spec(base_messages=base, harness=harness)
    )
    assert seen[0]["harness_version"] == 1
    assert seen[0]["harness_digest"] == harness.sha256


async def test_harness_absent_pins_nothing_keeping_pre_binding_replays() -> None:
    """No declaration means no harness keys: chains committed before the
    binding keep replaying byte-for-byte."""
    store, port = InMemoryDecisionStore(), StubToolPort()
    seen: list[dict[str, Any]] = []

    class RecordingSource(LoopScriptedSource):
        def responder_for(self, messages, input_refs):
            seen.append(dict(input_refs))
            return super().responder_for(messages, input_refs)

    await _controller(store, RecordingSource({(0, ""): _final("done")}), port).run(_spec())
    assert "harness_version" not in seen[0]
    assert "harness_digest" not in seen[0]


async def test_base_messages_must_equal_the_frozen_composition() -> None:
    """The rendered input cannot drift from the composition it is pinned to."""
    store, port = InMemoryDecisionStore(), StubToolPort()
    composed = [{"role": "system", "content": "You are a helper."}, {"role": "user", "content": "task"}]
    harness = HarnessSpec(
        1,
        (
            HarnessSection("persona", "You are a helper."),
            HarnessSection("task", [{"role": "user", "content": "task"}]),
        ),
    )
    with pytest.raises(DecisionValidationError, match="frozen harness composition"):
        await _controller(store, LoopScriptedSource({}), port).run(_spec(base_messages=composed[:-1], harness=harness))
    assert port.calls == []


async def test_declared_harness_replays_with_zero_fresh_work() -> None:
    """A committed bound chain restores under the same composition."""
    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    base = [{"role": "user", "content": "check the stock"}]
    harness = _harness_for(base)
    first = await _controller(store, LoopScriptedSource({(0, ""): _final("partial")}), port, ledger).run(
        _spec(run_ref, base_messages=base, harness=harness, limits=ControllerLimits(max_turns=2))
    )
    assert first.is_final_answer

    recovery = LoopScriptedSource({})  # any fresh decision attempt must fail the test
    fresh_port = StubToolPort()
    second = await _controller(store, recovery, fresh_port, ledger).run(
        _spec(run_ref, base_messages=base, harness=harness, limits=ControllerLimits(max_turns=2))
    )
    assert second.is_final_answer and second.final_text == "partial"
    assert recovery.model_asks == []  # the committed decision restored, zero model calls


async def test_harness_rewrite_under_same_version_conflicts_on_replay() -> None:
    """A rewritten composition re-presenting turn 1 is an explicit conflict."""
    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    base = [{"role": "user", "content": "check the stock"}]
    await _controller(store, LoopScriptedSource({(0, ""): _final("done")}), port, ledger).run(
        _spec(run_ref, base_messages=base, harness=_harness_for(base), limits=ControllerLimits(max_turns=1))
    )
    rewritten = HarnessSpec(
        1,
        (
            HarnessSection("persona", "You are someone else."),
            HarnessSection("task", [{"role": "user", "content": "check the stock"}]),
        ),
    )
    rewritten_messages = rewritten.compose_messages()
    with pytest.raises(DecisionConflictError):
        await _controller(store, LoopScriptedSource({}), StubToolPort(), ledger).run(
            _spec(run_ref, base_messages=rewritten_messages, harness=rewritten, limits=ControllerLimits(max_turns=1))
        )


async def test_rejected_model_call_classifies_stop_without_committing() -> None:
    """A guard-refused turn commits nothing and classifies a stop."""
    from hecate_runtime.harness_turn import ModelCallRejectedError

    class RejectingSource(LoopScriptedSource):
        def responder_for(self, messages, input_refs):
            class _Rejecting(DecisionResponder):
                async def respond(self, run_ref, decision_step_id, refs) -> DecisionProposal:
                    raise ModelCallRejectedError("pre_llm", "prompt injection suspected")

            return _Rejecting()

    store, port = InMemoryDecisionStore(), StubToolPort()
    events: list[dict[str, Any]] = []
    outcome = await _controller(store, RejectingSource({}), port).run(_spec(), emit=events.append)
    assert outcome.state == "stopped"
    assert outcome.reason == "model_call_rejected"
    assert outcome.decision_step_ids == ()  # the refused turn committed nothing
    assert store.lookup_calls >= 1 and port.calls == []
    terminal = [event for event in events if event["type"] == "controller/stopped"][-1]
    assert terminal["detail"] == "pre_llm: prompt injection suspected"


async def test_projection_policy_replay_skips_pipeline_and_conflicts_on_policy_drift() -> None:
    from hecate_runtime.context_policy import ContextChainFactory
    from hecate_runtime.harness_turn import HarnessTurnPipeline

    class ModelPort(StubToolPort):
        """Return a final answer and count fresh model calls."""

        async def llm_invoke_structured(self, messages, config):
            self.model_calls = getattr(self, "model_calls", 0) + 1
            yield {"content": "done"}

    class ReplayPipeline(HarnessTurnPipeline):
        """Fail if recovery attempts to project an accepted decision."""

        async def prepare(self, messages, tools):
            raise AssertionError("accepted decisions must not run the pipeline")

    def source(port, policy, *, replay=False):
        pipeline_type = ReplayPipeline if replay else HarnessTurnPipeline
        return PortTurnResponderSource(
            port,
            config={"model": "stub"},
            tool_definitions={},
            base_refs={},
            pipeline=pipeline_type(context_chain=ContextChainFactory(agent_policy=policy), model="stub"),
        )

    store, port = InMemoryDecisionStore(), ModelPort()
    spec = _spec()
    initial = source(port, ["tool_result_truncation"])
    first = await _controller(store, initial, port).run(spec)
    assert first.is_final_answer and port.model_calls == 1
    fresh = ModelPort()
    replayed = await _controller(store, source(fresh, ["tool_result_truncation"], replay=True), fresh).run(spec)
    assert replayed.final_text == "done" and not hasattr(fresh, "model_calls")
    with pytest.raises(DecisionConflictError):
        await _controller(store, source(fresh, ["round_window"], replay=True), fresh).run(spec)
    assert not hasattr(fresh, "model_calls")


# --- recoverable no-progress protection --------------------------------------------


class DigestLedger(StubLedger):
    """Records the canonical result digest like the SQL ledger does.

    First-run records return the digest; ledger resolutions replay the SAME
    stored value, so first-run and recovery must judge observations
    identically no matter how the displayed content re-serializes.
    """

    async def record_outcome(self, **kwargs):
        from dataclasses import replace

        result = kwargs.get("result")
        digest = (
            None
            if result is None
            else hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        )
        await super().record_outcome(**kwargs)
        state = self.states.get(kwargs["execution_id"])
        if state is not None and digest is not None:
            self.states[kwargs["execution_id"]] = replace(state, result_digest=digest)
        return digest


def _read_action(key: str, args: dict[str, Any]) -> AcceptedAction:
    return AcceptedAction(action_key=key, tool="web_search", arguments=args, side_effect_class="readonly")


def _write_action(key: str, args: dict[str, Any]) -> AcceptedAction:
    return AcceptedAction(action_key=key, tool="memory_add", arguments=args, side_effect_class="non_idempotent_write")


def _observed_turn(key: str, args: dict[str, Any]) -> DecisionProposal:
    return DecisionProposal(accepted_output={"text": "working"}, actions=(_read_action(key, args),))


def test_no_progress_policy_validation() -> None:
    from hecate_runtime.agent_controller import NoProgressPolicy

    assert NoProgressPolicy.from_declaration(None) is None
    policy = NoProgressPolicy.from_declaration({"limit": 2, "observations": ["web_search"]})
    assert policy is not None and policy.limit == 2 and policy.sha256
    for bad in (
        {"limit": 0, "observations": ["web_search"]},
        {"limit": True, "observations": ["web_search"]},
        {"limit": 2},
        {"limit": 2, "observations": []},
        {"limit": 2, "observations": ["web_search", "web_search"]},
        {"limit": 2, "observations": [" web_search"]},
        {"limit": 2, "observations": "web_search"},
        "no_progress",
    ):
        with pytest.raises(DecisionValidationError):
            NoProgressPolicy.from_declaration(bad)


async def test_consecutive_identical_read_observations_stop_before_the_next_ask() -> None:
    """Two identical recorded observations stop the loop without a third ask."""
    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), DigestLedger()
    source = LoopScriptedSource(
        {
            (0, ""): _observed_turn("a1", {"q": 1}),
            (1, _RESULT_SEARCH): _observed_turn("a2", {"q": 1}),
        }
    )
    outcome = await _controller(store, source, port, ledger).run(
        _spec(no_progress=NoProgressPolicy(limit=2, observations=("web_search",)), limits=ControllerLimits(max_turns=8))
    )
    assert outcome.state == "stopped" and outcome.reason == "no_progress_limit"
    assert outcome.final_text is None and outcome.turns == 2
    assert outcome.no_progress is not None
    assert outcome.no_progress["consecutive_turns"] == 2
    assert outcome.no_progress["limit"] == 2
    # The stop fired before the third model call: both asks were fresh turns.
    assert source.model_asks == [
        decision_step_id_for_turn("agent-turn", 1),
        decision_step_id_for_turn("agent-turn", 2),
    ]
    assert len(port.calls) == 2


async def test_changed_result_facts_keep_the_loop_moving() -> None:
    """Different recorded results are progress even with identical calls."""

    class CountingPort(StubToolPort):
        """Return a distinct payload per call: the world moved."""

        async def tool_execute(self, name, args, context=None):
            self.calls.append((name, args, dict(context or {})))
            return {"ok": True, "by": name, "call": len(self.calls)}

    store, ledger = InMemoryDecisionStore(), DigestLedger()
    port = CountingPort()
    first_read = str({"ok": True, "by": "web_search", "call": 1})
    second_read = str({"ok": True, "by": "web_search", "call": 2})
    source = LoopScriptedSource(
        {
            (0, ""): _observed_turn("a1", {"q": 1}),
            (1, first_read): _observed_turn("a2", {"q": 1}),
            (2, second_read): _final("changed under me"),
        }
    )
    outcome = await _controller(store, source, port, ledger).run(
        _spec(no_progress=NoProgressPolicy(limit=2, observations=("web_search",)), limits=ControllerLimits(max_turns=8))
    )
    assert outcome.is_final_answer and outcome.final_text == "changed under me"


async def test_unobserved_write_resets_the_observation_window() -> None:
    """A succeeded action outside the policy is change evidence, not noise."""
    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), DigestLedger()
    source = LoopScriptedSource(
        {
            (0, ""): _observed_turn("a1", {"q": 1}),
            (1, _RESULT_SEARCH): _observed_turn("a2", {"q": 1}),
            (2, _RESULT_SEARCH): DecisionProposal(
                accepted_output={"text": "working"}, actions=(_write_action("a3", {"q": 2}),)
            ),
            (3, _RESULT_OK): _observed_turn("a4", {"q": 1}),
            (4, _RESULT_SEARCH): _final("done after the write"),
        }
    )
    outcome = await _controller(store, source, port, ledger).run(
        _spec(no_progress=NoProgressPolicy(limit=3, observations=("web_search",)), limits=ControllerLimits(max_turns=8))
    )
    # The streak stands at 2 of 3 when the write lands; the write resets
    # the window, so the third identical read would have stopped without it.
    assert outcome.is_final_answer


async def test_input_revision_opens_a_fresh_window_without_resetting_the_budget() -> None:
    """A durable revision restarts observation; the turn budget stands."""
    from hecate_runtime.agent_controller import ControllerInputRevision

    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), DigestLedger()
    source = LoopScriptedSource(
        {
            (0, ""): _observed_turn("a1", {"q": 1}),
            (1, _RESULT_SEARCH): _observed_turn("a2", {"q": 1}),
            (2, _RESULT_SEARCH): _observed_turn("a3", {"q": 1}),
            (3, _RESULT_SEARCH): _final("done after the hint"),
        }
    )
    outcome = await _controller(store, source, port, ledger).run(
        _spec(
            no_progress=NoProgressPolicy(limit=2, observations=("web_search",)),
            limits=ControllerLimits(max_turns=8),
            input_revisions=(ControllerInputRevision("rev-1", {"hint": "look again"}, 2),),
        )
    )
    # Without the revision the loop stops at turn 2 (two identical reads);
    # the revision opens a fresh window, so turn 2 runs and only the NEXT
    # identical observation pair stops the loop at turn 3. The turn budget
    # advanced under the same policy throughout.
    assert outcome.state == "stopped" and outcome.reason == "no_progress_limit"
    assert outcome.turns == 3
    assert source.model_asks == [decision_step_id_for_turn("agent-turn", i) for i in (1, 2, 3)]


async def test_policy_binding_conflicts_on_same_version_rewrite() -> None:
    """A rewritten policy under the same version cannot reinterpret history."""
    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), DigestLedger()
    script = {
        (0, ""): _observed_turn("a1", {"q": 1}),
        (1, _RESULT_SEARCH): _observed_turn("a2", {"q": 1}),
        (2, _RESULT_SEARCH): _final("done"),
    }
    source = LoopScriptedSource(script)
    run_ref = _run_ref()
    first = await _controller(store, source, port, ledger).run(
        _spec(
            no_progress=NoProgressPolicy(limit=8, observations=("web_search",)),
            run_ref=run_ref,
            limits=ControllerLimits(max_turns=8),
        )
    )
    assert first.is_final_answer

    fresh_port = StubToolPort()
    recovery = LoopScriptedSource({})
    with pytest.raises(DecisionConflictError):
        await _controller(store, recovery, fresh_port, ledger).run(
            _spec(
                no_progress=NoProgressPolicy(limit=8, observations=("web_search", "read_file")),
                run_ref=run_ref,
                limits=ControllerLimits(max_turns=8),
            )
        )
    assert recovery.model_asks == [] and fresh_port.calls == []


async def test_no_policy_pins_nothing_and_a_later_policy_conflicts() -> None:
    """Undeclared tasks run unchanged; like every binding, adding one later
    to a committed chain conflicts instead of reinterpreting history."""
    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), DigestLedger()
    source = LoopScriptedSource(
        {
            (0, ""): _actions(("a1", "memory_add", {"q": 1})),
            (1, _RESULT_OK): _final("done"),
        }
    )
    run_ref = _run_ref()
    outcome = await _controller(store, source, port, ledger).run(
        _spec(run_ref=run_ref, limits=ControllerLimits(max_turns=8))
    )
    assert outcome.is_final_answer

    # The committed decisions pin no observation fields.
    for step in outcome.decision_step_ids:
        (recorded,) = [
            stored
            for (issuer, run, decision), stored in store._records.items()
            if run == run_ref.run_id and decision == step
        ]
        assert "no_progress_version" not in recorded.input_refs
        assert "no_progress_digest" not in recorded.input_refs

    # Like every binding, adding a policy to a committed chain conflicts on
    # the first replay instead of reinterpreting history.
    recovery = LoopScriptedSource({})
    with pytest.raises(DecisionConflictError):
        await _controller(store, recovery, StubToolPort(), ledger).run(
            _spec(
                no_progress=NoProgressPolicy(limit=1, observations=("web_search",)),
                run_ref=run_ref,
                limits=ControllerLimits(max_turns=1),
            )
        )
    assert recovery.model_asks == []


async def test_recovery_replays_stop_at_the_same_place() -> None:
    """A re-entered loop rebuilds the streak from durable facts and stops."""
    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), DigestLedger()
    script = {
        (0, ""): _observed_turn("a1", {"q": 1}),
        (1, _RESULT_SEARCH): _observed_turn("a2", {"q": 1}),
    }
    source = LoopScriptedSource(script)
    run_ref = _run_ref()
    first = await _controller(store, source, port, ledger).run(
        _spec(
            no_progress=NoProgressPolicy(limit=2, observations=("web_search",)),
            run_ref=run_ref,
            limits=ControllerLimits(max_turns=8),
        )
    )
    assert first.state == "stopped" and first.reason == "no_progress_limit"

    fresh_port = StubToolPort()
    recovery = LoopScriptedSource({})
    second = await _controller(store, recovery, fresh_port, ledger).run(
        _spec(
            no_progress=NoProgressPolicy(limit=2, observations=("web_search",)),
            run_ref=run_ref,
            limits=ControllerLimits(max_turns=8),
        )
    )
    assert second.state == "stopped" and second.reason == "no_progress_limit"
    assert recovery.model_asks == [] and fresh_port.calls == []
    assert second.decision_step_ids == first.decision_step_ids
    assert second.no_progress == first.no_progress


def test_unknown_no_progress_options_are_not_silently_ignored() -> None:
    with pytest.raises(DecisionValidationError, match="unknown"):
        NoProgressPolicy.from_declaration({"limit": 2, "observations": ["web_search"], "polling": True})


async def test_missing_observation_digest_requires_reconciliation() -> None:
    from hecate_runtime.accepted_decision import DecisionStoreUnavailableError

    source = LoopScriptedSource({(0, ""): _observed_turn("a1", {"q": 1})})
    with pytest.raises(DecisionStoreUnavailableError, match="observation"):
        await _controller(InMemoryDecisionStore(), source, StubToolPort(), StubLedger()).run(
            _spec(no_progress=NoProgressPolicy(limit=1, observations=("web_search",)))
        )


async def test_acknowledged_input_request_is_not_a_stagnant_observation(monkeypatch) -> None:
    from hecate_runtime.agent_controller import ControllerInputRevision

    envelope = {"status": "input_required", "contract": {"fields": ["region"]}}

    class InputPort(StubToolPort):
        """Replay the confirmed input request after a human wake."""

        async def tool_execute(self, name, args, context=None):
            return envelope

    source = LoopScriptedSource(
        {(0, ""): _observed_turn("input-request", {}), (1, json.dumps(envelope)): _final("human input consumed")}
    )
    store, ledger = InMemoryDecisionStore(), DigestLedger()
    spec = _spec(
        no_progress=NoProgressPolicy(limit=1, observations=("web_search",)),
        input_revisions=(ControllerInputRevision("human-wake", {"region": "EU"}, 2),),
    )
    outcome = await _controller(store, source, InputPort(), ledger).run(spec)
    assert outcome.is_final_answer

    async def redact_replay(self, result, **kwargs):
        return {**result, "content": "request details withheld by current governance"}

    monkeypatch.setattr(ToolCallExecutor, "_maybe_govern_recovery", redact_replay)
    recovery, fresh = LoopScriptedSource({}), StubToolPort()
    restored = await _controller(store, recovery, fresh, ledger).run(spec)
    assert restored.is_final_answer
    assert recovery.model_asks == [] and fresh.calls == []


async def test_declared_observation_scope_excludes_legitimate_polling() -> None:
    """A polling tool outside the observation set remains bounded by normal limits."""
    read = AcceptedAction("poll-1", "read_file", {}, "readonly")
    reply = str({"ok": True, "by": "read_file"})
    source = LoopScriptedSource(
        {
            (0, ""): DecisionProposal({"text": "poll"}, (read,)),
            (1, reply): DecisionProposal({"text": "poll"}, (AcceptedAction("poll-2", "read_file", {}, "readonly"),)),
            (2, reply): _final("poll complete"),
        }
    )
    outcome = await _controller(InMemoryDecisionStore(), source, StubToolPort(), DigestLedger()).run(
        _spec(no_progress=NoProgressPolicy(limit=1, observations=("web_search",)))
    )
    assert outcome.is_final_answer


async def test_observation_policy_cannot_attest_its_own_write_tool() -> None:
    with pytest.raises(DecisionValidationError, match="confirmed read-only"):
        await _controller(InMemoryDecisionStore(), LoopScriptedSource({}), StubToolPort(), DigestLedger()).run(
            _spec(no_progress=NoProgressPolicy(limit=1, observations=("memory_add",)))
        )


async def test_channel_backfill_cannot_replace_the_ledger_observation_digest(monkeypatch) -> None:
    """Governed channel text may differ; the raw ledger fact must remain stable."""
    from hecate_runtime.action_ledger import ToolExecutionResolution, ToolExecutionState, tool_arguments_digest

    store, ledger = InMemoryDecisionStore(), DigestLedger()
    source = LoopScriptedSource({(0, ""): _observed_turn("read", {})})
    spec = _spec(no_progress=NoProgressPolicy(limit=1, observations=("web_search",)))
    first = await _controller(store, source, StubToolPort(), ledger).run(spec)

    async def event_resolution(*args):
        return ToolExecutionResolution(
            ToolExecutionState.SUCCEEDED,
            arguments_digest=tool_arguments_digest({}),
            result_digest="e" * 64,
            tool_name="web_search",
            result_content="previously governed channel content",
        )

    monkeypatch.setattr("hecate_runtime.tool_call_executor.resolve_tool_execution_state", event_resolution)
    fresh = StubToolPort()
    recovered = await _controller(store, LoopScriptedSource({}), fresh, ledger).run(spec)
    assert recovered.no_progress == first.no_progress
    assert fresh.calls == []


# -- bounded continuation after acceptance failure ---------------------------


class TurnKeyedSource(TurnResponderSource):
    """Script the loop by durable turn index, not by projected position.

    Continuation rounds reuse the same projected shape (no tool messages),
    so the position key cannot tell the rejected candidate's turn from the
    corrected turn; the durable turn index can.
    """

    def __init__(self, by_turn: dict[int, DecisionProposal], *, frozen: dict[str, Any] | None = None):
        self.by_turn = by_turn
        self._frozen = frozen or {"config": "v1"}
        self.turns: list[list[dict[str, Any]]] = []
        self.model_asks: list[str] = []

    def frozen_refs(self) -> dict[str, Any]:
        return dict(self._frozen)

    def responder_for(self, messages: list[dict[str, Any]], input_refs: dict[str, Any]) -> DecisionResponder:
        self.turns.append(json.loads(json.dumps(messages)))
        turn = input_refs["turn"]
        proposal = self.by_turn.get(turn)
        source = self

        class _OneShot(DecisionResponder):
            async def respond(self, run_ref, decision_step_id, refs) -> DecisionProposal:
                if proposal is None:
                    raise AssertionError(f"scripted loop decided an unsolicited turn {turn}")
                source.model_asks.append(decision_step_id)
                return proposal

        return _OneShot()


def _continuation_spec(run_ref=None, **kwargs) -> ControllerRunSpec:
    from hecate_runtime.delivery_acceptance import AcceptanceDeclaration

    return _spec(
        run_ref,
        acceptance=AcceptanceDeclaration(version=2, required_checks=({"kind": "content_contains", "value": "ok"},)),
        **kwargs,
    )


def _entry(turn: int, text_digest: str, *, attempt: int = 1, feedback: dict[str, Any] | None = None) -> Any:
    from hecate_runtime.agent_controller import ControllerContinuation

    return ControllerContinuation(
        attempt=attempt,
        candidate_decision_step_id=decision_step_id_for_turn("agent-turn", turn),
        candidate_digest=text_digest,
        effective_turn=turn + 1,
        feedback=feedback or {"attempt": attempt, "instruction": "revise"},
    )


def test_continuation_policy_validation() -> None:
    from hecate_runtime.agent_controller import ControllerContinuationPolicy

    policy = ControllerContinuationPolicy.from_declaration({"max_continuations": 2})
    assert policy is not None and policy.max_continuations == 2
    assert ControllerContinuationPolicy.from_declaration(None) is None
    for bad in (
        {"max_continuations": True},
        {"max_continuations": 0},
        {"max_continuations": 1.5},
        {"max_continuations": 2, "unknown": 1},
        "bounded",
        {"max_continuations": 2, "version": True},
    ):
        with pytest.raises(DecisionValidationError):
            ControllerContinuationPolicy.from_declaration(bad)


def test_continuation_entry_validation() -> None:
    from hecate_runtime.agent_controller import ControllerContinuation

    with pytest.raises(DecisionValidationError):
        ControllerContinuation(
            attempt=0, candidate_decision_step_id="t-000001", candidate_digest="a" * 64, effective_turn=2, feedback={}
        )
    with pytest.raises(DecisionValidationError):
        ControllerContinuation(
            attempt=1, candidate_decision_step_id="t-000001", candidate_digest="xyz", effective_turn=2, feedback={}
        )
    with pytest.raises(DecisionValidationError):
        ControllerContinuation(
            attempt=1, candidate_decision_step_id="t-000001", candidate_digest="a" * 64, effective_turn=1, feedback=[]
        )
    record = {
        "attempt": 1,
        "candidate_decision_step_id": "agent-turn-000004",
        "candidate_digest": "b" * 64,
        "effective_turn": 5,
        "feedback": {"k": 1},
    }
    entry = ControllerContinuation.from_record(record)
    assert entry.effective_turn == 5
    for corrupt in (
        {**record, "extra": 1},
        {k: v for k, v in record.items() if k != "feedback"},
        {**record, "feedback": "text"},
    ):
        with pytest.raises(DecisionValidationError):
            ControllerContinuation.from_record(corrupt)


async def test_continuation_policy_requires_declared_acceptance() -> None:
    from hecate_runtime.agent_controller import ControllerContinuationPolicy

    store, port = InMemoryDecisionStore(), StubToolPort()
    source = TurnKeyedSource({1: _final("bad")})
    with pytest.raises(DecisionValidationError, match="acceptance"):
        await _controller(store, source, port).run(
            _spec(continuation_policy=ControllerContinuationPolicy(max_continuations=1))
        )


async def test_continuation_projects_feedback_and_continues_the_chain() -> None:
    """A matched candidate is projected with its feedback; the chain continues."""
    from hecate_runtime.agent_controller import ControllerContinuationPolicy

    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    policy = ControllerContinuationPolicy(max_continuations=1)
    first = await _controller(store, TurnKeyedSource({1: _final("bad draft")}), port, ledger).run(
        _continuation_spec(run_ref, continuation_policy=policy)
    )
    assert first.is_final_answer and first.final_decision_step_id == decision_step_id_for_turn("agent-turn", 1)

    source = TurnKeyedSource({2: _final("ok result")})
    entry = _entry(
        1,
        first.final_decision_digest,
        feedback={"attempt": 1, "max_continuations": 1, "failed_checks": [{"kind": "content_contains"}]},
    )
    events: list[dict[str, Any]] = []
    outcome = await _controller(store, source, StubToolPort(), ledger).run(
        _continuation_spec(run_ref, continuation_policy=policy, continuations=(entry,)),
        emit=events.append,
    )
    assert outcome.is_final_answer and outcome.final_text == "ok result"
    assert outcome.turns == 2
    # Turn 1 replayed from storage; only the corrected turn was asked.
    assert source.model_asks == [decision_step_id_for_turn("agent-turn", 2)]
    # The corrected turn sees the rejected answer and the internal feedback
    # — an explicit control message, never a human input revision.
    feedback_messages = [
        message
        for message in source.turns[1]
        if isinstance(message.get("content"), str) and "continuation_feedback" in message["content"]
    ]
    assert len(feedback_messages) == 1
    assert json.loads(feedback_messages[0]["content"])["continuation_feedback"]["failed_checks"] == [
        {"kind": "content_contains"}
    ]
    assert all("provided_input" not in str(message.get("content", "")) for message in source.turns[1])
    assert any(event["type"] == "controller/continuation" and event["attempt"] == 1 for event in events)


async def test_double_continuation_rounds_project_in_order() -> None:
    from hecate_runtime.agent_controller import ControllerContinuationPolicy

    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    policy = ControllerContinuationPolicy(max_continuations=2)
    first = await _controller(store, TurnKeyedSource({1: _final("draft one")}), port, ledger).run(
        _continuation_spec(run_ref, continuation_policy=policy)
    )
    second = await _controller(store, TurnKeyedSource({2: _final("draft two")}), port, ledger).run(
        _continuation_spec(
            run_ref,
            continuation_policy=policy,
            continuations=(_entry(1, first.final_decision_digest, feedback={"round": 1}),),
        )
    )
    source = TurnKeyedSource({3: _final("ok result")})
    outcome = await _controller(store, source, StubToolPort(), ledger).run(
        _continuation_spec(
            run_ref,
            continuation_policy=ControllerContinuationPolicy(max_continuations=2),
            continuations=(
                _entry(1, first.final_decision_digest, attempt=1, feedback={"round": 1}),
                _entry(2, second.final_decision_digest, attempt=2, feedback={"round": 2}),
            ),
        )
    )
    assert outcome.is_final_answer and outcome.turns == 3
    assert source.model_asks == [decision_step_id_for_turn("agent-turn", 3)]
    rounds = [
        json.loads(message["content"])["continuation_feedback"]["round"]
        for message in source.turns[2]
        if isinstance(message.get("content"), str) and "continuation_feedback" in message["content"]
    ]
    assert rounds == [1, 2]


async def test_candidate_digest_mismatch_is_corruption_not_revision() -> None:
    from hecate_runtime.agent_controller import ControllerContinuationPolicy

    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    await _controller(store, TurnKeyedSource({1: _final("bad draft")}), port, ledger).run(
        _continuation_spec(run_ref, continuation_policy=ControllerContinuationPolicy(max_continuations=1))
    )
    source = TurnKeyedSource({2: _final("tampered")})
    with pytest.raises(DecisionStoreUnavailableError):
        await _controller(store, source, StubToolPort(), ledger).run(
            _continuation_spec(
                run_ref,
                continuation_policy=ControllerContinuationPolicy(max_continuations=1),
                continuations=(_entry(1, "f" * 64),),
            )
        )
    assert source.model_asks == []


async def test_continuation_policy_binding_conflicts_on_rewrite() -> None:
    """A policy rewritten under the same version cannot reinterpret decisions."""
    from hecate_runtime.agent_controller import ControllerContinuationPolicy

    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    policy = ControllerContinuationPolicy(max_continuations=1)
    first = await _controller(store, TurnKeyedSource({1: _final("bad draft")}), port, ledger).run(
        _continuation_spec(run_ref, continuation_policy=policy)
    )
    spec = _continuation_spec(
        run_ref,
        continuation_policy=policy,
        continuations=(_entry(1, first.final_decision_digest),),
    )
    await _controller(store, TurnKeyedSource({2: _final("fixed")}), StubToolPort(), ledger).run(spec)
    rewritten = _continuation_spec(
        run_ref,
        continuation_policy=ControllerContinuationPolicy(max_continuations=3),
        continuations=(_entry(1, first.final_decision_digest),),
    )
    with pytest.raises(DecisionConflictError):
        await _controller(store, TurnKeyedSource({}), StubToolPort(), ledger).run(rewritten)


async def test_continuation_without_policy_pins_nothing_on_decisions() -> None:
    """A chain committed without the policy replays byte-for-byte later."""
    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    await _controller(store, TurnKeyedSource({1: _final("answer")}), port, ledger).run(_continuation_spec(run_ref))
    # The same chain re-presented with a continuation policy cannot silently
    # append the binding: every committed decision would need new refs.
    from hecate_runtime.agent_controller import ControllerContinuationPolicy

    with pytest.raises(DecisionConflictError):
        await _controller(store, TurnKeyedSource({}), StubToolPort(), ledger).run(
            _continuation_spec(run_ref, continuation_policy=ControllerContinuationPolicy(max_continuations=1))
        )


async def test_continuation_feedback_preserves_no_progress_window() -> None:
    """Internal feedback preserves the existing observation streak and budgets."""

    from hecate_runtime.agent_controller import ControllerContinuationPolicy

    policy = NoProgressPolicy(limit=2, observations=("web_search",))
    continuation = ControllerContinuationPolicy(max_continuations=1)
    store, ledger = InMemoryDecisionStore(), DigestLedger()
    run_ref = _run_ref()
    observed = _observed_turn("read", {"q": 1})
    first = await _controller(
        store, TurnKeyedSource({1: observed, 2: _final("bad draft")}), StubToolPort(), ledger
    ).run(_continuation_spec(run_ref, no_progress=policy, continuation_policy=continuation))
    assert first.is_final_answer and first.final_decision_step_id == decision_step_id_for_turn("agent-turn", 2)
    source = TurnKeyedSource({3: observed, 4: observed})
    outcome = await _controller(store, source, StubToolPort(), ledger).run(
        _continuation_spec(
            run_ref,
            no_progress=policy,
            continuation_policy=continuation,
            continuations=(_entry(2, first.final_decision_digest),),
        )
    )
    assert outcome.state == "stopped" and outcome.reason == "no_progress_limit"
    assert outcome.no_progress is not None
    assert outcome.no_progress["through_turn"] == 3
    assert outcome.no_progress["consecutive_turns"] == 2
    assert outcome.no_progress["last_decision_step_id"] == decision_step_id_for_turn("agent-turn", 3)


async def test_continuation_turn_budget_is_shared_across_rounds() -> None:
    """Correction turns count against the same durable max_turns."""
    from hecate_runtime.agent_controller import ControllerContinuationPolicy

    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    first = await _controller(store, TurnKeyedSource({1: _final("bad draft")}), port, ledger).run(
        _continuation_spec(
            run_ref,
            limits=ControllerLimits(max_turns=2),
            continuation_policy=ControllerContinuationPolicy(max_continuations=1),
        )
    )
    assert first.is_final_answer
    # max_turns=2: the candidate consumed turn 1; only turn 2 remains.
    source = TurnKeyedSource({2: _final("ok result")})
    outcome = await _controller(store, source, StubToolPort(), ledger).run(
        _continuation_spec(
            run_ref,
            limits=ControllerLimits(max_turns=2),
            continuation_policy=ControllerContinuationPolicy(max_continuations=1),
            continuations=(_entry(1, first.final_decision_digest),),
        )
    )
    assert outcome.is_final_answer and outcome.turns == 2

    # One turn further out: the correction cannot fit the budget anymore.
    run_ref2 = _run_ref()
    store2, ledger2 = InMemoryDecisionStore(), StubLedger()
    candidate = await _controller(store2, TurnKeyedSource({1: _final("bad draft")}), StubToolPort(), ledger2).run(
        _continuation_spec(
            run_ref2,
            limits=ControllerLimits(max_turns=1),
            continuation_policy=ControllerContinuationPolicy(max_continuations=1),
        )
    )
    assert candidate.is_final_answer
    outcome2 = await _controller(store2, TurnKeyedSource({}), StubToolPort(), ledger2).run(
        _continuation_spec(
            run_ref2,
            limits=ControllerLimits(max_turns=1),
            continuation_policy=ControllerContinuationPolicy(max_continuations=1),
            continuations=(_entry(1, candidate.final_decision_digest),),
        )
    )
    assert outcome2.state == "stopped" and outcome2.reason == "turn_limit"
    assert outcome2.turns == 1


async def test_consumed_continuation_feedback_cannot_be_rewritten() -> None:
    from hecate_runtime.agent_controller import ControllerContinuationPolicy

    store, ledger = InMemoryDecisionStore(), StubLedger()
    run_ref = _run_ref()
    policy = ControllerContinuationPolicy(max_continuations=1)
    first = await _controller(store, TurnKeyedSource({1: _final("draft")}), StubToolPort(), ledger).run(
        _continuation_spec(run_ref, continuation_policy=policy)
    )
    entry = _entry(1, first.final_decision_digest)
    await _controller(store, TurnKeyedSource({2: _final("ok")}), StubToolPort(), ledger).run(
        _continuation_spec(run_ref, continuation_policy=policy, continuations=(entry,))
    )
    source = TurnKeyedSource({})
    with pytest.raises(DecisionConflictError):
        await _controller(store, source, StubToolPort(), ledger).run(
            _continuation_spec(
                run_ref,
                continuation_policy=policy,
                continuations=(_entry(1, first.final_decision_digest, feedback={"instruction": "different"}),),
            )
        )
    assert source.model_asks == []


@pytest.mark.parametrize("missing_policy,attempt", [(True, 1), (False, 2)])
async def test_invalid_continuation_authority_stops_before_model(missing_policy, attempt) -> None:
    from hecate_runtime.agent_controller import ControllerContinuationPolicy

    source = TurnKeyedSource({1: _final("draft")})
    with pytest.raises(DecisionValidationError):
        await _controller(InMemoryDecisionStore(), source, StubToolPort()).run(
            _continuation_spec(
                continuation_policy=None if missing_policy else ControllerContinuationPolicy(max_continuations=1),
                continuations=(_entry(1, "a" * 64, attempt=attempt),),
            )
        )
    assert source.model_asks == []
