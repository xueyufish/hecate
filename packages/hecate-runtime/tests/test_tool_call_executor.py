"""Contract and recovery parity for direct tool calls and Worker delegation."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path
from typing import Any

import pytest
from hecate_runtime.action_ledger import (
    ActionClaimVerdict,
    ActionLedgerHook,
    ToolExecutionResolution,
    ToolExecutionState,
    stable_execution_id,
)
from hecate_runtime.errors import ExecutionStoppedError
from hecate_runtime.eventstore import Event, EventType, InMemoryEventStore
from hecate_runtime.guardrail import GuardrailAction, GuardrailResult, PostToolHook, PreToolHook
from hecate_runtime.middleware import Chain, Phase, StageDecision
from hecate_runtime.ports import StubRuntimePort
from hecate_runtime.sandbox_router import SandboxEnforcementRouter
from hecate_runtime.tool_access import AccessDecision, ApprovalDecision
from hecate_runtime.tool_call_executor import ToolCallExecutor
from hecate_runtime.workers.tool_worker import ToolWorker


class StubToolPort(StubRuntimePort):
    """Record dispatches, contexts and simulated business outcomes."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, dict, dict]] = []
        self.sandbox_calls: list[tuple[str, dict, dict]] = []
        self.trace: list[str] = []

    async def tool_execute(self, name, args, context=None):
        """Record the business call before returning or raising."""
        self.calls.append((name, args, dict(context or {})))
        self.trace.append("dispatch")
        if self.error is not None:
            raise self.error
        return {"ok": True}

    async def tool_execute_sandbox(self, name, args, context=None):
        """Record sandbox dispatch separately."""
        self.sandbox_calls.append((name, args, dict(context or {})))
        return {"sandboxed": True}


class StubPolicy:
    """Return one deterministic access decision."""

    def __init__(self, decision: AccessDecision) -> None:
        self.decision = decision
        self.calls: list[dict] = []

    def evaluate(self, tool_meta, rules, eval_context, arguments=None):
        """Capture attribution and return the configured decision."""
        self.calls.append(eval_context)
        return self.decision


class StubApprovalCallback:
    """Capture approval binding and return a deterministic answer."""

    def __init__(self, approved: bool) -> None:
        self.approved = approved
        self.contexts: list[dict] = []

    async def request_approval(self, tool_name, arguments, risk_level, context):
        """Record the request without any external service."""
        self.contexts.append(context)
        return ApprovalDecision(approved=self.approved, reason="test approval", approval_ref="approval-1")


class StubPreHook(PreToolHook):
    """Block legacy pre-tool calls and record invocation."""

    def __init__(self) -> None:
        self.calls = 0

    async def on_pre_tool_call(self, name, arguments, context):
        """Reject the call before a business effect."""
        self.calls += 1
        return GuardrailResult(action=GuardrailAction.BLOCK, reason="legacy block")


class StubPostHook(PostToolHook):
    """Sanitize legacy tool results."""

    async def on_post_tool_call(self, name, result, context):
        """Replace the result consumed by the caller."""
        return GuardrailResult(action=GuardrailAction.SANITIZE, modified_data={"result": "safe result"})


class StubEvidenceTracker:
    """Capture evidence provenance and the result visible after hooks."""

    def __init__(self) -> None:
        self.records: list[dict] = []

    def match_existing(self, name, arguments):
        """No prior evidence is present."""
        return None

    def capture(self, **kwargs):
        """Capture the evidence produced by the executor."""
        self.records.append(kwargs)


class StubLedgerHook(ActionLedgerHook):
    """Model atomic claims and persistent outcomes across executor instances."""

    def __init__(self, trace: list[str] | None = None) -> None:
        self.states: dict[str, ToolExecutionResolution] = {}
        self.claims: list[dict] = []
        self.outcomes: list[dict] = []
        self.trace = trace if trace is not None else []
        self.lookup_fails = False
        self.claim_fails = False
        self.outcome_fails = False
        self.lock = asyncio.Lock()

    async def resolve(self, *, session_id, execution_id):
        """Distinguish an absent action from an unavailable store."""
        if self.lookup_fails:
            raise OSError("ledger lookup unavailable")
        return self.states.get(execution_id, ToolExecutionResolution(ToolExecutionState.NEVER_STARTED))

    async def record_claim(self, **kwargs):
        """Allow only one first claimant for an execution."""
        if self.claim_fails:
            raise OSError("ledger claim unavailable")
        async with self.lock:
            self.claims.append(kwargs)
            key = kwargs["execution_id"]
            existing = self.states.get(key)
            if existing is not None:
                return ActionClaimVerdict(claimed=False, resolution=existing)
            self.states[key] = ToolExecutionResolution(
                ToolExecutionState.CLAIMED,
                arguments_digest=kwargs["arguments_digest"],
                tool_name=kwargs["tool_name"],
            )
            self.trace.append("claim")
            return ActionClaimVerdict(claimed=True)

    async def record_outcome(self, **kwargs):
        """Leave the action claimed when a receipt cannot be committed."""
        if self.outcome_fails:
            raise OSError("ledger outcome unavailable")
        self.outcomes.append(kwargs)
        self.trace.append("outcome")
        result = kwargs["result"]
        self.states[kwargs["execution_id"]] = ToolExecutionResolution(
            ToolExecutionState.OUTCOME_UNKNOWN
            if kwargs["status"] == "unknown"
            else ToolExecutionState(kwargs["status"]),
            arguments_digest=kwargs["arguments_digest"],
            tool_name=kwargs["tool_name"],
            result_content=result if isinstance(result, str) else json.dumps(result),
        )


@pytest.fixture(params=[ToolCallExecutor, ToolWorker], ids=["executor", "worker"])
def entry_class(request):
    """Exercise the public entry point of each consumer."""
    return request.param


async def _invoke(
    component, session, *, name="biz_tool", call_id="call-1", arguments=None, context=None, metadata=None
):
    call = {"id": call_id, "function": {"name": name, "arguments": {} if arguments is None else arguments}}
    execution_context = {"session_id": session, **(metadata or {})}
    if isinstance(component, ToolCallExecutor):
        return await component.execute_call(call, context or {}, execution_context, node_id="caller-label")
    result = await component.execute(
        "caller-label",
        {},
        {**(context or {}), "messages": [{"role": "assistant", "tool_calls": [call]}]},
        execution_context,
    )
    return result.channel_updates["messages"][0]


async def test_success_and_provenance_without_graph_metadata(entry_class):
    port = StubToolPort()
    store = InMemoryEventStore()
    tracker = StubEvidenceTracker()
    session = uuid.uuid4()
    component = entry_class(port=port, event_store=store)
    metadata = {"evidence_tracker": tracker, "agent_id": "agent", "workspace_id": "workspace"}
    result = await _invoke(component, session, metadata=metadata)
    execution_id = stable_execution_id(session, "call-1")
    assert result == {"role": "tool", "tool_call_id": "call-1", "content": str({"ok": True})}
    assert len(port.calls) == 1
    assert port.calls[0][2]["invocation_id"] == execution_id
    assert port.calls[0][2]["agent_id"] == "agent"
    assert port.calls[0][2]["workspace_id"] == "workspace"
    events = await store.get_events(session)
    assert [e.event_type for e in events] == [EventType.TOOL_CALL, EventType.TOOL_RESULT]
    assert all(e.superstep == 0 and e.node_id is None for e in events)
    assert all(e.payload["execution_id"] == execution_id for e in events)
    assert tracker.records[0]["node_id"] == "caller-label"
    assert tracker.records[0]["raw_content"] == {"ok": True}
    assert tracker.records[0]["superstep"] == 0


async def test_explicit_graph_metadata_is_preserved(entry_class):
    store = InMemoryEventStore()
    session = uuid.uuid4()
    component = entry_class(port=StubToolPort(), event_store=store)
    await _invoke(component, session, metadata={"superstep": 7, "trace_id": "trace"})
    events = await store.get_events(session)
    assert all(e.superstep == 7 and e.trace_id == "trace" and e.node_id is None for e in events)


@pytest.mark.parametrize("arguments", ['{"broken":', "[]", {"q": float("nan")}])
async def test_invalid_arguments_do_not_dispatch(entry_class, arguments):
    port = StubToolPort()
    component = entry_class(port=port)
    result = await _invoke(component, uuid.uuid4(), arguments=arguments)
    assert result["is_error"] is True
    assert port.calls == []


async def test_policy_deny_identical_across_entries(entry_class):
    port, store = StubToolPort(), InMemoryEventStore()
    policy = StubPolicy(AccessDecision.DENY)
    component = entry_class(port=port, event_store=store, access_policy=policy)
    session = uuid.uuid4()
    result = await _invoke(component, session)
    assert result["is_error"] is True
    assert "denied by access policy" in result["content"]
    assert port.calls == []
    assert [e.event_type for e in await store.get_events(session)] == [EventType.CHANNEL_WRITE_REJECTED]


async def test_approval_required_without_callback_identical(entry_class):
    port, store = StubToolPort(), InMemoryEventStore()
    component = entry_class(port=port, event_store=store, access_policy=StubPolicy(AccessDecision.REQUIRE_APPROVAL))
    session = uuid.uuid4()
    result = await _invoke(component, session)
    assert result["is_error"] is True
    assert "requires approval" in result["content"]
    assert port.calls == []
    assert [e.event_type for e in await store.get_events(session)] == [EventType.CHANNEL_WRITE_REJECTED]


@pytest.mark.parametrize("approved", [False, True])
async def test_approval_binding_and_result(entry_class, approved):
    port = StubToolPort()
    callback = StubApprovalCallback(approved)
    component = entry_class(
        port=port,
        access_policy=StubPolicy(AccessDecision.REQUIRE_APPROVAL),
        approval_callback=callback,
    )
    session = uuid.uuid4()
    result = await _invoke(component, session, arguments={"q": 1})
    assert callback.contexts[0]["invocation_id"] == stable_execution_id(session, "call-1")
    assert callback.contexts[0]["tool_call_id"] == "call-1"
    if approved:
        assert port.calls[0][2]["approval_ref"] == "approval-1"
        assert not result.get("is_error")
    else:
        assert result["is_error"] is True
        assert port.calls == []


@pytest.mark.parametrize("mode", ["legacy_pre", "legacy_post", "chain_pre", "chain_post", "chain_precedence"])
async def test_middleware_and_legacy_hooks(entry_class, mode):
    port = StubToolPort()
    pre_hook = StubPreHook()
    dependencies: dict[str, Any] = {}
    if mode in ("legacy_pre", "chain_precedence"):
        dependencies["pre_tool_hook"] = pre_hook
    elif mode == "legacy_post":
        dependencies["post_tool_hook"] = StubPostHook()
    if mode.startswith("chain"):
        phase = Phase.TOOL_RESULT if mode == "chain_post" else Phase.TOOL_PRE_EXECUTE
        chain = Chain(phase=phase)

        async def stage(data, call_next):
            if mode == "chain_pre":
                return StageDecision.block("test-stage", "chain block"), None
            if mode == "chain_post":
                return StageDecision.sanitize("test-stage", {"result": "safe result"}), data
            outcome = await call_next(data)
            return outcome.decision, outcome.result

        chain.add_stage("test-stage", stage)
        dependencies["middleware_chains"] = {phase: chain}
    component = entry_class(port=port, **dependencies)
    result = await _invoke(component, uuid.uuid4())
    if mode in ("legacy_pre", "chain_pre"):
        assert result["is_error"] is True
        assert port.calls == []
    else:
        assert len(port.calls) == 1
        assert result["content"] == ("safe result" if mode.endswith("post") else str({"ok": True}))
    assert pre_hook.calls == (1 if mode == "legacy_pre" else 0)


async def test_sandbox_routing_identical(entry_class):
    port = StubToolPort()
    component = entry_class(
        port=port,
        access_policy=StubPolicy(AccessDecision.EXECUTE_SANDBOX),
        sandbox_enforcement=SandboxEnforcementRouter(enabled=True),
    )
    result = await _invoke(component, uuid.uuid4(), name="bash", context={"sandbox_enabled": True})
    assert result["content"] == str({"sandboxed": True})
    assert port.calls == []
    assert len(port.sandbox_calls) == 1
    assert port.sandbox_calls[0][2]["_sandbox_enforcement"] is True


async def test_tool_error_is_failed_receipt(entry_class):
    store, port = InMemoryEventStore(), StubToolPort(error=RuntimeError("boom"))
    component = entry_class(port=port, event_store=store)
    session = uuid.uuid4()
    result = await _invoke(component, session)
    assert result["is_error"] is True
    assert result["content"] == "boom"
    events = await store.get_events(session)
    assert [e.event_type for e in events] == [EventType.TOOL_CALL, EventType.TOOL_RESULT]
    assert events[-1].payload["status"] == "failed"


@pytest.mark.parametrize("error", [TimeoutError("remote timeout"), ConnectionError("remote connection lost")])
async def test_unknown_outcome_stops_and_is_not_retried(entry_class, error):
    store, port = InMemoryEventStore(), StubToolPort(error=error)
    component = entry_class(port=port, event_store=store)
    session = uuid.uuid4()
    for _ in range(2):
        with pytest.raises(ExecutionStoppedError, match="needs_review") as stopped:
            await _invoke(component, session)
        assert stopped.value.action_ref == stable_execution_id(session, "call-1")
    assert len(port.calls) == 1
    assert (await store.get_events(session))[-1].payload["status"] == "unknown"


async def test_event_log_backfills_real_content(entry_class):
    store, port = InMemoryEventStore(), StubToolPort()
    component = entry_class(port=port, event_store=store)
    session = uuid.uuid4()
    first = await _invoke(component, session)
    await store.append(
        Event(
            session_id=session,
            superstep=0,
            event_type=EventType.CHANNEL_WRITE,
            payload={"channel": "messages", "value": [first]},
        )
    )
    second = await _invoke(component, session)
    assert second == first
    assert len(port.calls) == 1


async def test_ledger_claim_precedes_dispatch_and_recovers_without_event_log(entry_class):
    port = StubToolPort()
    hook = StubLedgerHook(port.trace)
    session = uuid.uuid4()
    first = await _invoke(entry_class(port=port, action_hook=hook), session)
    second = await _invoke(entry_class(port=port, action_hook=hook), session)
    assert first["content"] == str({"ok": True})
    assert json.loads(second["content"]) == {"ok": True}
    assert port.trace == ["claim", "dispatch", "outcome"]
    assert len(hook.claims) == len(hook.outcomes) == len(port.calls) == 1
    assert hook.claims[0]["execution_id"] == hook.outcomes[0]["execution_id"] == stable_execution_id(session, "call-1")
    assert hook.claims[0]["session_id"] == str(session)
    assert hook.claims[0]["arguments_digest"] == hook.outcomes[0]["arguments_digest"]


@pytest.mark.parametrize("failure", ["lookup", "claim"])
@pytest.mark.parametrize("tool_name", ["biz_tool", "web_search"])
async def test_ledger_unavailability_preserves_existing_retry_rules(entry_class, failure, tool_name):
    hook, port = StubLedgerHook(), StubToolPort()
    hook.lookup_fails = failure == "lookup"
    hook.claim_fails = failure == "claim"
    component = entry_class(port=port, action_hook=hook)
    result = await _invoke(component, uuid.uuid4(), name=tool_name)
    if tool_name == "web_search":
        assert len(port.calls) == 1
        assert not result.get("is_error")
    else:
        assert port.calls == []
        assert result["is_error"] is True
        assert "withheld" in result["content"]


async def test_outcome_write_failure_does_not_replay_side_effect(entry_class):
    hook, port = StubLedgerHook(), StubToolPort()
    hook.outcome_fails = True
    session = uuid.uuid4()
    first = await _invoke(entry_class(port=port, action_hook=hook), session)
    assert first["content"] == str({"ok": True})
    with pytest.raises(ExecutionStoppedError, match="needs_review"):
        await _invoke(entry_class(port=port, action_hook=hook), session)
    assert len(port.calls) == 1
    assert hook.states[stable_execution_id(session, "call-1")].state is ToolExecutionState.CLAIMED


async def test_concurrent_duplicate_claims_do_not_duplicate_side_effect(entry_class):
    ready, release = asyncio.Event(), asyncio.Event()

    class StubRacingLedger(StubLedgerHook):
        def __init__(self):
            super().__init__()
            self.lookups = 0

        async def resolve(self, **kwargs):
            """Make both recovery pre-filters miss before either claims."""
            self.lookups += 1
            if self.lookups == 2:
                ready.set()
            await ready.wait()
            return ToolExecutionResolution(ToolExecutionState.NEVER_STARTED)

    class StubBlockingPort(StubToolPort):
        async def tool_execute(self, name, args, context=None):
            """Keep the winning effect in flight until the loser is observed."""
            result = await super().tool_execute(name, args, context)
            await release.wait()
            return result

    port, hook = StubBlockingPort(), StubRacingLedger()
    session = uuid.uuid4()
    tasks = [asyncio.create_task(_invoke(entry_class(port=port, action_hook=hook), session)) for _ in range(2)]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED, timeout=5)
        assert len(done) == len(pending) == 1
        loser = done.pop()
        assert isinstance(loser.exception(), ExecutionStoppedError)
        assert len(port.calls) == 1
    finally:
        release.set()
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    assert len(hook.claims) == 2
    assert len(hook.outcomes) == len(port.calls) == 1
    assert sum(isinstance(result, dict) for result in outcomes) == 1


async def test_batch_worker_drains_started_calls_before_raising():
    """A terminal sibling cannot finish the worker before an ongoing effect."""
    started, failed, release, finished = (asyncio.Event() for _ in range(4))
    calls: list[str] = []

    class StubBatchPort(StubToolPort):
        async def tool_execute(self, name, args, context=None):
            """Hold the slow effect beyond the other call's unknown outcome."""
            calls.append(name)
            if name == "slow_tool":
                started.set()
                await release.wait()
                finished.set()
                return "slow result"
            await started.wait()
            failed.set()
            raise TimeoutError("unknown effect")

    worker = ToolWorker(port=StubBatchPort())
    calls_payload = [
        {"id": "fast", "name": "unknown_tool", "arguments": {}},
        {"id": "slow", "name": "slow_tool", "arguments": {}},
    ]
    execution = asyncio.create_task(
        worker.execute(
            "tools",
            {},
            {"messages": [{"role": "assistant", "tool_calls": calls_payload}]},
            {"session_id": uuid.uuid4()},
        )
    )
    try:
        await asyncio.wait_for(failed.wait(), timeout=5)
        # Let task completion callbacks run while the business effect stays blocked.
        for _ in range(3):
            await asyncio.sleep(0)
        assert not execution.done()
        assert not finished.is_set()
    finally:
        release.set()
        with pytest.raises(ExecutionStoppedError, match="needs_review"):
            await asyncio.wait_for(execution, timeout=5)
    assert calls == ["unknown_tool", "slow_tool"]
    assert finished.is_set()


async def test_batch_result_order_follows_input_not_completion():
    first_started, second_done = asyncio.Event(), asyncio.Event()

    class StubReorderedPort(StubToolPort):
        async def tool_execute(self, name, args, context=None):
            """Make the second call finish before the first."""
            if name == "first_tool":
                first_started.set()
                await second_done.wait()
                return "first result"
            await first_started.wait()
            second_done.set()
            return "second result"

    worker = ToolWorker(port=StubReorderedPort())
    result = await worker.execute(
        "tools",
        {},
        {
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "first", "name": "first_tool", "arguments": {}},
                        {"id": "second", "name": "second_tool", "arguments": {}},
                    ],
                }
            ]
        },
        {"session_id": uuid.uuid4()},
    )
    assert [message["content"] for message in result.channel_updates["messages"]] == ["first result", "second result"]
    assert [message["tool_call_id"] for message in result.channel_updates["messages"]] == ["first", "second"]


def test_executor_loads_and_runs_without_graph_imports(tmp_path):
    """A cold interpreter rejects graph and Worker imports, including lazy ones."""
    script = textwrap.dedent("""
        import asyncio
        import importlib.abc
        import sys
        import uuid

        blocked = (
            "hecate_runtime.pregel", "hecate_runtime.compiler", "hecate_runtime.channel",
            "hecate_runtime.worker", "hecate_runtime.workers",
            "hecate_runtime.scheduler", "hecate_runtime.checkpoint",
        )
        class BlockGraph(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if any(fullname == prefix or fullname.startswith(prefix + ".") for prefix in blocked):
                    raise ImportError("graph/Worker import forbidden: " + fullname)
        sys.meta_path.insert(0, BlockGraph())

        from hecate_runtime.tool_call_executor import ToolCallExecutor
        from hecate_runtime.ports import StubRuntimePort
        from hecate_runtime.eventstore import InMemoryEventStore
        async def run():
            session = uuid.uuid4()
            executor = ToolCallExecutor(StubRuntimePort(), event_store=InMemoryEventStore())
            result = await executor.execute_call(
                {"id": "cold-call", "name": "web_search", "arguments": {"q": "test"}},
                {}, {"session_id": session}, "standalone",
            )
            assert result["tool_call_id"] == "cold-call"
            assert not result.get("is_error")
        asyncio.run(run())
        assert not any(
            name == prefix or name.startswith(prefix + ".")
            for name in sys.modules for prefix in blocked
        )
        print("cold graph-free execution passed")
    """)
    runtime_src = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(  # noqa: S603 - trusted interpreter and repository-local probe; no shell
        [sys.executable, "-c", f"import sys; sys.path.insert(0, {runtime_src!r});\n{script}"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "cold graph-free execution passed" in result.stdout


def test_sandbox_router_old_path_forwards_to_same_implementation():
    from hecate_runtime.workers.sandbox_router import SandboxEnforcementRouter as LegacyRouter

    assert LegacyRouter is SandboxEnforcementRouter
