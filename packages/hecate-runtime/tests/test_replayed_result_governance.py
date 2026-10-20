"""Replayed tool-result governance on the shared executor (step7).

The ledger mirrors the PRE-governance result on first execution, so a
succeeded receipt replayed after a restart (or lost channel history)
would hand the model text the first run blocked or sanitized. Covers the
source-marked backfill path: ledger-sourced content re-runs the
first-execution PostTool pipeline (TOOL_RESULT chain first, legacy hook
fallback) at the recovery pre-check and ledger claim-race exits, while
channel-log content — already the first-run governed message — passes
through ungoverned. Business dispatches stay at zero on replay and the
ledger outcome is never rewritten.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import replace
from typing import Any

import pytest
from hecate_runtime.accepted_action_step import AcceptedActionStep
from hecate_runtime.accepted_decision import (
    AcceptedAction,
    AcceptedDecisionRecord,
    AcceptedDecisionStore,
    DecisionOwnership,
    DecisionValidationError,
    RunRef,
    canonical_decision_digest,
)
from hecate_runtime.action_ledger import (
    ActionClaimVerdict,
    ActionLedgerHook,
    ToolExecutionResolution,
    ToolExecutionState,
    stable_execution_id,
    tool_arguments_digest,
)
from hecate_runtime.eventstore import Event, EventType, InMemoryEventStore
from hecate_runtime.guardrail import GuardrailAction, GuardrailResult, PostToolHook
from hecate_runtime.middleware import Chain, Phase, StageDecision
from hecate_runtime.ports import StubRuntimePort
from hecate_runtime.tool_access import AccessDecision, ApprovalDecision
from hecate_runtime.tool_call_executor import ToolCallExecutor
from hecate_runtime.workers.tool_worker import ToolWorker

RAW_RESULT = "SECRET-VALUE classified payload"


class StubToolPort(StubRuntimePort):
    """Count business dispatches; replay must add none."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, dict]] = []

    async def tool_execute(self, name, args, context=None):
        self.calls.append((name, args, dict(context or {})))
        return RAW_RESULT


class ScriptedPostHook(PostToolHook):
    """Legacy hook with a scripted outcome and call counting."""

    def __init__(self, action: GuardrailAction, *, masked: str = "masked-by-hook") -> None:
        self.action = action
        self.masked = masked
        self.calls = 0

    async def on_post_tool_call(self, name, result, context):
        self.calls += 1
        if self.action is GuardrailAction.BLOCK:
            return GuardrailResult(action=GuardrailAction.BLOCK, reason="hook policy")
        if self.action is GuardrailAction.SANITIZE:
            return GuardrailResult(action=GuardrailAction.SANITIZE, modified_data={"result": self.masked})
        return GuardrailResult(action=GuardrailAction.ALLOW)


class FlakyPostHook(PostToolHook):
    """Governance that fails — the replay path must fail closed."""

    def __init__(self) -> None:
        self.calls = 0

    async def on_post_tool_call(self, name, result, context):
        self.calls += 1
        raise RuntimeError("guardrail backend down")


class ScriptedLedger(ActionLedgerHook):
    """Ledger double with persistent outcomes and programmable resolve."""

    def __init__(self) -> None:
        self.states: dict[str, ToolExecutionResolution] = {}
        self.claims: list[dict] = []
        self.lock = asyncio.Lock()
        self.first_resolve_never_started = False
        self._resolve_calls = 0

    def seed_succeeded(
        self,
        session: uuid.UUID,
        tc_id: str,
        content: str | None,
        *,
        arguments: dict | None = None,
        tool_name: str = "biz_tool",
    ) -> str:
        execution_id = stable_execution_id(session, tc_id)
        self.states[execution_id] = ToolExecutionResolution(
            ToolExecutionState.SUCCEEDED,
            arguments_digest=tool_arguments_digest({} if arguments is None else arguments),
            tool_name=tool_name,
            result_content=content,
        )
        return execution_id

    async def resolve(self, *, session_id, execution_id):
        self._resolve_calls += 1
        if self.first_resolve_never_started and self._resolve_calls == 1:
            return ToolExecutionResolution(ToolExecutionState.NEVER_STARTED)
        return self.states.get(execution_id, ToolExecutionResolution(ToolExecutionState.NEVER_STARTED))

    async def record_claim(self, **kwargs):
        async with self.lock:
            self.claims.append(kwargs)
            existing = self.states.get(kwargs["execution_id"])
            if existing is not None:
                return ActionClaimVerdict(claimed=False, resolution=existing)
            self.states[kwargs["execution_id"]] = ToolExecutionResolution(
                ToolExecutionState.CLAIMED,
                arguments_digest=kwargs["arguments_digest"],
                tool_name=kwargs["tool_name"],
            )
            return ActionClaimVerdict(claimed=True)

    async def record_outcome(self, **kwargs):
        result = kwargs.get("result")
        self.states[kwargs["execution_id"]] = ToolExecutionResolution(
            ToolExecutionState.OUTCOME_UNKNOWN
            if kwargs["status"] == "unknown"
            else ToolExecutionState(kwargs["status"]),
            arguments_digest=kwargs["arguments_digest"],
            tool_name=kwargs["tool_name"],
            result_content=result if isinstance(result, str) else json.dumps(result),
        )


def _chain(action: GuardrailAction) -> tuple[Chain, dict[str, int]]:
    """TOOL_RESULT chain with one scripted stage and a call counter."""
    chain = Chain(phase=Phase.TOOL_RESULT)
    counter = {"calls": 0}

    async def stage(data, call_next):
        counter["calls"] += 1
        if action is GuardrailAction.BLOCK:
            return StageDecision.block("replay-stage", "chain policy"), None
        if action is GuardrailAction.SANITIZE:
            masked = str(data["result"]).replace("SECRET-VALUE ", "")
            return StageDecision.sanitize("replay-stage", {"result": masked}), data
        return StageDecision.allow(), await call_next(data)

    chain.add_stage("replay-stage", stage)
    return chain, counter


def _executor(
    port: StubToolPort,
    ledger: ScriptedLedger,
    *,
    chain: Chain | None = None,
    post_hook: PostToolHook | None = None,
    event_store: InMemoryEventStore | None = None,
) -> ToolCallExecutor:
    dependencies: dict[str, Any] = {
        "port": port,
        "event_store": event_store if event_store is not None else InMemoryEventStore(),
        "action_hook": ledger,
    }
    if chain is not None:
        dependencies["middleware_chains"] = {Phase.TOOL_RESULT: chain}
    if post_hook is not None:
        dependencies["post_tool_hook"] = post_hook
    return ToolCallExecutor(**dependencies)


async def _replay(
    executor: ToolCallExecutor,
    session: uuid.UUID,
    *,
    messages: list[dict] | None = None,
) -> dict[str, Any]:
    context = {"messages": messages} if messages is not None else {}
    return await executor.execute_call(
        {"id": "call-1", "function": {"name": "biz_tool", "arguments": {}}},
        context,
        {"session_id": session},
        "caller-label",
    )


@pytest.mark.parametrize("mode", ["block", "sanitize", "allow"])
async def test_precheck_ledger_backfill_replays_chain(mode):
    """The recovery pre-check governs ledger content through the chain."""
    port, ledger = StubToolPort(), ScriptedLedger()
    session = uuid.uuid4()
    ledger.seed_succeeded(session, "call-1", RAW_RESULT)
    chain, counter = _chain(GuardrailAction(mode))
    executor = _executor(port, ledger, chain=chain)

    result = await _replay(executor, session)

    if mode == "block":
        assert result["content"] == "Result sanitized: chain policy"
    elif mode == "sanitize":
        assert result["content"] == "classified payload"
        assert "SECRET-VALUE" not in result["content"]
    else:
        assert result["content"] == RAW_RESULT
    assert counter["calls"] == 1
    assert port.calls == []  # replay never re-dispatches the business tool
    assert ledger.states[stable_execution_id(session, "call-1")].result_content == RAW_RESULT


@pytest.mark.parametrize("mode", ["block", "sanitize", "allow"])
@pytest.mark.parametrize("race", [False, True], ids=["precheck", "claim-race"])
async def test_precheck_ledger_backfill_replays_legacy_hook(mode, race):
    """Without a chain the legacy PostToolHook governs the replay."""
    port, ledger = StubToolPort(), ScriptedLedger()
    session = uuid.uuid4()
    ledger.seed_succeeded(session, "call-1", RAW_RESULT)
    ledger.first_resolve_never_started = race
    hook = ScriptedPostHook(GuardrailAction(mode))
    executor = _executor(port, ledger, post_hook=hook)

    result = await _replay(executor, session)

    assert hook.calls == 1
    if mode == "block":
        assert result["content"] == "Result sanitized: hook policy"
    elif mode == "sanitize":
        assert result["content"] == "masked-by-hook"
    else:
        assert result["content"] == RAW_RESULT
    assert port.calls == []


async def test_replay_without_governance_passes_through():
    """No chain and no matching hook: replayed content is unchanged."""
    port, ledger = StubToolPort(), ScriptedLedger()
    session = uuid.uuid4()
    ledger.seed_succeeded(session, "call-1", RAW_RESULT)
    executor = _executor(port, ledger)

    result = await _replay(executor, session)

    assert result == {"role": "tool", "tool_call_id": "call-1", "content": RAW_RESULT}
    assert port.calls == []


async def test_channel_history_content_is_not_re_governed():
    """Channel-log content is already the first-run governed message."""
    port, ledger = StubToolPort(), ScriptedLedger()
    session = uuid.uuid4()
    ledger.seed_succeeded(session, "call-1", None)  # ledger has no content
    store = InMemoryEventStore()
    governed = "Result sanitized: first run"
    await store.append(
        Event(
            session_id=session,
            superstep=0,
            event_type=EventType.CHANNEL_WRITE,
            payload={"channel": "messages", "value": [{"role": "tool", "tool_call_id": "call-1", "content": governed}]},
        )
    )
    chain, counter = _chain(GuardrailAction.BLOCK)
    executor = _executor(port, ledger, chain=chain, event_store=store)

    result = await _replay(executor, session)

    assert result["content"] == governed
    assert counter["calls"] == 0  # governed-once content passes through
    assert port.calls == []


async def test_context_message_copy_is_not_re_governed():
    """A governed tool message already in context is returned unmarked."""
    port, ledger = StubToolPort(), ScriptedLedger()
    session = uuid.uuid4()
    ledger.seed_succeeded(session, "call-1", RAW_RESULT)
    governed = "Result sanitized: first run"
    chain, counter = _chain(GuardrailAction.BLOCK)
    executor = _executor(port, ledger, chain=chain)

    result = await _replay(
        executor,
        session,
        messages=[{"role": "tool", "tool_call_id": "call-1", "content": governed}],
    )

    assert result["content"] == governed
    assert counter["calls"] == 0
    assert port.calls == []


async def test_claim_race_backfill_is_governed():
    """Losing the ledger claim to a concurrent successor governs backfill."""
    port, ledger = StubToolPort(), ScriptedLedger()
    session = uuid.uuid4()
    ledger.seed_succeeded(session, "call-1", RAW_RESULT)
    ledger.first_resolve_never_started = True  # pre-check resolves before the rival wins
    chain, counter = _chain(GuardrailAction.BLOCK)
    executor = _executor(port, ledger, chain=chain)

    result = await _replay(executor, session)

    assert result["content"] == "Result sanitized: chain policy"
    assert counter["calls"] == 1
    assert port.calls == []
    assert len(ledger.claims) == 1


async def test_governance_failure_propagates_without_raw_content():
    """A failing guard on the replay path fails closed: no content at all."""
    port, ledger = StubToolPort(), ScriptedLedger()
    session = uuid.uuid4()
    ledger.seed_succeeded(session, "call-1", RAW_RESULT)
    executor = _executor(port, ledger, post_hook=FlakyPostHook())

    with pytest.raises(RuntimeError, match="guardrail backend down"):
        await _replay(executor, session)
    assert port.calls == []


async def test_first_run_then_replay_dispatches_exactly_once():
    """End-to-end: one business call, governed first run, governed replay."""
    port, ledger = StubToolPort(), ScriptedLedger()
    session = uuid.uuid4()
    first_chain, first_counter = _chain(GuardrailAction.ALLOW)
    first = _executor(port, ledger, chain=first_chain)

    first_result = await _replay(first, session)
    assert first_result["content"] == RAW_RESULT
    assert len(port.calls) == 1

    replay_chain, replay_counter = _chain(GuardrailAction.BLOCK)
    second = _executor(port, ledger, chain=replay_chain)  # fresh instance, same ledger
    replay_result = await _replay(second, session)

    assert replay_result["content"] == "Result sanitized: chain policy"
    assert len(port.calls) == 1  # the business effect happened exactly once
    assert replay_counter["calls"] == 1
    assert ledger.states[stable_execution_id(session, "call-1")].result_content == RAW_RESULT


async def test_repeated_sanitize_does_not_amplify():
    """Re-governing already-masked content stays masked, never leaks."""
    port, ledger = StubToolPort(), ScriptedLedger()
    session = uuid.uuid4()
    ledger.seed_succeeded(session, "call-1", "SECRET-VALUE rest")
    chain, _ = _chain(GuardrailAction.SANITIZE)
    executor = _executor(port, ledger, chain=chain)

    first = await _replay(executor, session)
    second = await _replay(executor, session)

    assert first["content"] == "rest"
    assert second["content"] == "rest"
    assert "SECRET-VALUE" not in second["content"]


@pytest.mark.parametrize("mode", ["chain", "legacy"])
async def test_empty_ledger_result_still_runs_output_governance(mode):
    """An empty string is a recorded result, not a missing receipt."""
    port, ledger = StubToolPort(), ScriptedLedger()
    session = uuid.uuid4()
    ledger.seed_succeeded(session, "call-1", "")
    hook = ScriptedPostHook(GuardrailAction.BLOCK)
    chain, counter = _chain(GuardrailAction.BLOCK)
    executor = _executor(port, ledger, chain=chain if mode == "chain" else None, post_hook=hook)

    result = await _replay(executor, session)

    assert result["content"].startswith("Result sanitized:")
    assert (counter["calls"] if mode == "chain" else hook.calls) == 1
    assert port.calls == []


async def test_claim_race_preserves_governed_context_message():
    """Losing a claim must not govern an existing channel result twice."""
    port, ledger = StubToolPort(), ScriptedLedger()
    session = uuid.uuid4()
    ledger.seed_succeeded(session, "call-1", RAW_RESULT)
    ledger.first_resolve_never_started = True
    hook = ScriptedPostHook(GuardrailAction.SANITIZE, masked="second-pass")
    executor = _executor(port, ledger, post_hook=hook)
    governed = {"role": "tool", "tool_call_id": "call-1", "content": "first-pass"}

    result = await _replay(executor, session, messages=[governed])

    assert result == governed
    assert hook.calls == 0
    assert len(ledger.claims) == 1
    assert port.calls == []


@pytest.mark.parametrize("race", [False, True], ids=["precheck", "claim-race"])
async def test_replay_governance_receives_authoritative_invocation_context(race):
    """Result policies receive the same trusted identity as first execution."""

    class ContextHook(PostToolHook):
        """Reject a result whose identity does not match this execution."""

        async def on_post_tool_call(self, name, result, context):
            """Check server-derived correlation and caller attribution."""
            assert context["tool_call_id"] == "call-1"
            assert context["invocation_id"] == stable_execution_id(session, "call-1")
            assert context["workspace_id"] == "workspace"
            return GuardrailResult(action=GuardrailAction.ALLOW)

    port, ledger = StubToolPort(), ScriptedLedger()
    session = uuid.uuid4()
    ledger.seed_succeeded(session, "call-1", RAW_RESULT)
    ledger.first_resolve_never_started = race
    executor = _executor(port, ledger, post_hook=ContextHook())
    context = {"invocation_id": "untrusted", "workspace_id": "workspace"}

    result = await executor.execute_call(
        {"id": "call-1", "function": {"name": "biz_tool", "arguments": {}}},
        context,
        {"session_id": session},
    )

    assert result["content"] == RAW_RESULT
    assert context == {"invocation_id": "untrusted", "workspace_id": "workspace"}
    assert port.calls == []


@pytest.mark.parametrize("race", [False, True], ids=["precheck", "claim-race"])
@pytest.mark.parametrize("action", list(GuardrailAction))
async def test_replay_chain_precedes_legacy_hook(race, action):
    """Each reachable ledger exit uses the chain exactly once, never both guards."""
    port, ledger = StubToolPort(), ScriptedLedger()
    session = uuid.uuid4()
    ledger.seed_succeeded(session, "call-1", RAW_RESULT)
    ledger.first_resolve_never_started = race
    chain, counter = _chain(action)
    hook = FlakyPostHook()
    executor = _executor(port, ledger, chain=chain, post_hook=hook)

    result = await _replay(executor, session)

    expected = {
        GuardrailAction.ALLOW: RAW_RESULT,
        GuardrailAction.BLOCK: "Result sanitized: chain policy",
        GuardrailAction.SANITIZE: "classified payload",
    }
    assert result == {"role": "tool", "tool_call_id": "call-1", "content": expected[action]}
    assert counter["calls"] == 1
    assert hook.calls == 0
    assert port.calls == []


async def test_nonmatching_legacy_hook_does_not_govern_replay():
    """Matcher-scoped hooks retain their scope during replay."""
    port, ledger = StubToolPort(), ScriptedLedger()
    session = uuid.uuid4()
    ledger.seed_succeeded(session, "call-1", RAW_RESULT)
    hook = FlakyPostHook()
    hook.matcher = "other_tool"
    result = await _replay(_executor(port, ledger, post_hook=hook), session)
    assert result["content"] == RAW_RESULT
    assert hook.calls == 0


@pytest.mark.parametrize("action", list(GuardrailAction))
async def test_worker_replay_governs_without_repeating_business_or_claim(action):
    """The graph consumer observes governed results through its public entry."""
    port, ledger = StubToolPort(), ScriptedLedger()
    session = uuid.uuid4()
    call = {"id": "call-1", "function": {"name": "biz_tool", "arguments": {}}}
    context = {"messages": [{"role": "assistant", "tool_calls": [call]}]}
    first = ToolWorker(port, action_hook=ledger)
    await first.execute("tools", {}, context, {"session_id": session})
    assert len(port.calls) == 1
    claims_before = len(ledger.claims)
    hook = ScriptedPostHook(action)
    restored = ToolWorker(port, action_hook=ledger, post_tool_hook=hook)

    outcome = await restored.execute("tools", {}, context, {"session_id": session})

    content = outcome.channel_updates["messages"][0]["content"]
    assert content == (
        RAW_RESULT
        if action is GuardrailAction.ALLOW
        else "masked-by-hook"
        if action is GuardrailAction.SANITIZE
        else "Result sanitized: hook policy"
    )
    assert hook.calls == 1
    assert len(port.calls) == 1
    assert len(ledger.claims) == claims_before


class StubAcceptedLedger(ScriptedLedger):
    """Require the host's Run ownership on accepted dispatch."""

    revoked = False

    async def validate_accepted_dispatch(self, *, run_ref, ownership, execution_id):
        """Reject a revoked owner before any recovered payload is exposed."""
        if self.revoked or ownership != OWNER:
            raise DecisionValidationError("owner revoked")


class StubDecisionStore(AcceptedDecisionStore):
    """Expose an already accepted action; recovery cannot commit a replacement."""

    def __init__(self, record):
        self.record = record

    async def lookup(self, run_ref, decision_step_id):
        """Return the original record for its exact identity."""
        assert run_ref == self.record.run_ref
        assert decision_step_id == self.record.decision_step_id
        return self.record

    async def commit(self, record, ownership=None):
        """Reject any attempt to accept another decision in a dispatch test."""
        raise AssertionError("unexpected commit")


class StubApprovalPolicy:
    """Require explicit approval on the first business execution."""

    def evaluate(self, *args, **kwargs):
        """Request approval without side effects."""
        return AccessDecision.REQUIRE_APPROVAL


class StubApproval:
    """Count approval consumption across first execution and replay."""

    def __init__(self):
        self.calls = 0

    async def request_approval(self, *args, **kwargs):
        """Grant the first execution only; replay must not request again."""
        self.calls += 1
        assert self.calls == 1
        return ApprovalDecision(approved=True, reason="test", approval_ref="approved-once")


OWNER = DecisionOwnership("lease", 1, "holder")


@pytest.mark.parametrize("mode", ["block", "sanitize", "allow", "error", "revoked"])
async def test_accepted_step_replay_keeps_business_success_and_current_ownership(mode):
    """Accepted recovery uses PostTool without consuming another approval or claim."""
    port, ledger, approval = StubToolPort(), StubAcceptedLedger(), StubApproval()
    run = RunRef("test", uuid.uuid4().hex)
    record = AcceptedDecisionRecord(run, "step", actions=(AcceptedAction("a", "biz_tool", {}),))
    record = replace(record, accepted_at="2026-01-01T00:00:00+00:00", content_digest=canonical_decision_digest(record))
    store = StubDecisionStore(record)
    first = AcceptedActionStep(
        store,
        ToolCallExecutor(
            port,
            action_hook=ledger,
            access_policy=StubApprovalPolicy(),
            approval_callback=approval,
        ),
    )
    first_outcome = await first.dispatch(run_ref=run, decision_step_id="step", action_key="a", ownership=OWNER)
    assert first_outcome.status == "succeeded"
    assert len(port.calls) == approval.calls == len(ledger.claims) == 1
    state_before = dict(ledger.states)
    hook = (
        FlakyPostHook()
        if mode == "error"
        else ScriptedPostHook(GuardrailAction.BLOCK if mode == "revoked" else GuardrailAction(mode))
    )
    ledger.revoked = mode == "revoked"
    restored = AcceptedActionStep(
        store,
        ToolCallExecutor(
            port,
            action_hook=ledger,
            access_policy=StubApprovalPolicy(),
            approval_callback=approval,
            post_tool_hook=hook,
        ),
    )
    if mode == "revoked":
        with pytest.raises(DecisionValidationError, match="owner revoked"):
            await restored.dispatch(run_ref=run, decision_step_id="step", action_key="a", ownership=OWNER)
        assert hook.calls == 0
    elif mode == "error":
        outcome = await restored.dispatch(run_ref=run, decision_step_id="step", action_key="a", ownership=OWNER)
        assert outcome.status == "stopped"
        assert outcome.stop_reason == "reconciliation_required"
        assert outcome.result is None
    else:
        outcome = await restored.dispatch(run_ref=run, decision_step_id="step", action_key="a", ownership=OWNER)
        assert outcome.status == "succeeded"
        assert outcome.execution_id == first_outcome.execution_id
        assert outcome.result is not None
        assert outcome.result["content"] == (
            RAW_RESULT
            if mode == "allow"
            else "masked-by-hook"
            if mode == "sanitize"
            else "Result sanitized: hook policy"
        )
    assert ledger.states == state_before
    assert len(port.calls) == approval.calls == len(ledger.claims) == 1


async def test_in_lock_channel_backfill_preserves_governed_content():
    """A receipt arriving after the pre-check uses its already governed channel result."""
    session = uuid.uuid4()
    execution_id = stable_execution_id(session, "call-1")
    governed = {"role": "tool", "tool_call_id": "call-1", "content": "already-masked"}

    class ArrivingReceiptStore(InMemoryEventStore):
        """Make the winning worker's settled receipt visible at the locked check."""

        reads = 0

        async def get_events(self, session_id: uuid.UUID, from_version: int = 0) -> list[Event]:
            """Expose the completed result only after recovery's initial read."""
            self.reads += 1
            if self.reads == 1:
                return []
            return [
                Event(
                    session_id=session_id,
                    superstep=0,
                    event_type=EventType.TOOL_RESULT,
                    payload={
                        "execution_id": execution_id,
                        "tool_name": "biz_tool",
                        "arguments_digest": tool_arguments_digest({}),
                        "status": "succeeded",
                    },
                ),
                Event(
                    session_id=session_id,
                    superstep=0,
                    event_type=EventType.CHANNEL_WRITE,
                    payload={"channel": "messages", "value": [governed]},
                ),
            ]

    port, hook, event_store = StubToolPort(), FlakyPostHook(), ArrivingReceiptStore()
    executor = ToolCallExecutor(port, event_store=event_store, post_tool_hook=hook)
    result = await _replay(executor, session)
    assert result == governed
    assert event_store.reads >= 3
    assert hook.calls == 0
    assert port.calls == []


@pytest.mark.parametrize("race", [False, True], ids=["precheck", "claim-race"])
async def test_replay_chain_error_does_not_fall_back_to_raw_result(race):
    """A failed result middleware cannot be replaced by a permissive legacy guard."""
    port, ledger = StubToolPort(), ScriptedLedger()
    session = uuid.uuid4()
    execution_id = ledger.seed_succeeded(session, "call-1", RAW_RESULT)
    ledger.first_resolve_never_started = race
    chain = Chain(phase=Phase.TOOL_RESULT)

    async def failing_stage(data, call_next):
        raise RuntimeError("result policy unavailable")

    chain.add_stage("unavailable", failing_stage)
    hook = ScriptedPostHook(GuardrailAction.ALLOW)
    with pytest.raises(RuntimeError, match="result policy unavailable"):
        await _replay(_executor(port, ledger, chain=chain, post_hook=hook), session)
    assert hook.calls == 0
    assert port.calls == []
    assert ledger.states[execution_id].state is ToolExecutionState.SUCCEEDED
