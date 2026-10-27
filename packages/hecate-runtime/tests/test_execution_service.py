"""Shared runtime execution-service behavior.

Covers three layers of the execution-driver seam:

- Service behavior over a stub driver (outcome classification, cancel paths).
- A blocked-import environment test: with ``hecate_runtime.pregel`` unimportable,
  the service still loads and drives a non-graph stub — the seam's reason to exist.
- Driver contract tests parametrized over the production Pregel driver and the
  stub (event forwarding, early close, opaque resume passthrough).
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import uuid
from typing import Any

import pytest
from hecate_runtime.checkpoint import InMemoryCheckpointStore
from hecate_runtime.compiler import CompiledGraph
from hecate_runtime.execution_driver import ExecutionDriver, PregelExecutionDriver
from hecate_runtime.execution_service import (
    RuntimeEventDecision,
    RuntimeExecutionRequest,
    RuntimeExecutionState,
    RuntimeScheduleCancelledError,
    runtime_execution_service,
)
from hecate_runtime.pregel import PregelRuntime
from hecate_runtime.types import ChannelDef, ChannelType, Edge, NodeConfig, NodeType
from hecate_runtime.worker import Worker, WorkerResult


class _StubDriver(ExecutionDriver):
    def __init__(self, events: list[dict[str, Any]], *, fail: bool = False, delay_after_events: float = 0) -> None:
        self._events = events
        self._fail = fail
        self._delay_after_events = delay_after_events

    async def execute(
        self,
        session_id: uuid.UUID,
        initial_input: dict[str, Any] | None = None,
        execution_mode: str = "conversational",
        stream_mode: Any = None,
        resume_value: Any = None,
    ):
        for event in self._events:
            yield event
            if self._delay_after_events:
                await asyncio.sleep(self._delay_after_events)
        if self._fail:
            raise RuntimeError("engine failed")


def _request(
    runtime: ExecutionDriver, initial_input: dict[str, Any] | None = None, **kwargs: Any
) -> RuntimeExecutionRequest:
    return RuntimeExecutionRequest(
        runtime=runtime,
        session_id=uuid.uuid4(),
        initial_input=initial_input or {},
        **kwargs,
    )


async def test_successful_run_captures_events() -> None:
    observed: list[Any] = []

    def observe(event: Any) -> RuntimeEventDecision:
        observed.append(event)
        return RuntimeEventDecision.CONTINUE

    result = await runtime_execution_service.execute(
        _request(
            _StubDriver([{"type": "update", "value": 1}, {"type": "values", "value": 2}]),
            observer=observe,
        )
    )
    assert result.state is RuntimeExecutionState.SUCCEEDED
    assert result.events == tuple(observed)


async def test_engine_failure_is_run_fact() -> None:
    result = await runtime_execution_service.execute(_request(_StubDriver([], fail=True)))
    assert result.state is RuntimeExecutionState.FAILED
    assert result.error == "engine failed"


async def test_decision_conflicts_remain_reconciliation_facts() -> None:
    """Hosts must distinguish corrupt/conflicting history from an ordinary failure."""
    from hecate_runtime.accepted_decision import DecisionConflictError

    class ConflictingDriver(_StubDriver):
        async def execute(self, *args, **kwargs):
            raise DecisionConflictError("step", "old", "new")
            yield  # pragma: no cover

    result = await runtime_execution_service.execute(_request(ConflictingDriver([])))
    assert result.state is RuntimeExecutionState.FAILED
    assert result.detail["requires_reconciliation"] is True


async def test_cooperative_cancel_stops_after_observed_event() -> None:
    result = await runtime_execution_service.execute(
        _request(
            _StubDriver([{"type": "update"}, {"type": "values"}]),
            should_cancel=lambda: True,
        )
    )
    assert result.state is RuntimeExecutionState.CANCELLED
    assert result.cancel_requested is True
    assert result.events == ({"type": "update"},)


async def test_observer_can_stop_as_unknown() -> None:
    result = await runtime_execution_service.execute(
        _request(
            _StubDriver([{"type": "interrupt"}, {"type": "values"}]),
            observer=lambda _event: RuntimeEventDecision.STOP_UNKNOWN,
        )
    )
    assert result.state is RuntimeExecutionState.UNKNOWN
    assert result.detail["reason"] == "observer requested unknown stop"


async def test_schedule_cancel_returns_unknown_partial_result() -> None:
    task = asyncio.create_task(
        runtime_execution_service.execute(_request(_StubDriver([{"type": "update"}], delay_after_events=10)))
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(RuntimeScheduleCancelledError):
        await task


# --- Blocked-import environment: the service must load and drive a stub ---


_BLOCKED_MODULES = ("hecate_runtime.pregel", "hecate.runtime.pregel")
_FRESH_MODULES = ("hecate_runtime.execution_service", "hecate_runtime.execution_driver")


def _block_pregel_imports() -> dict[str, Any]:
    """Purge and block the Pregel modules; return the saved state for restore."""
    saved: dict[str, Any] = {}
    for name in (*_BLOCKED_MODULES, *_FRESH_MODULES):
        if name in sys.modules:
            saved[name] = sys.modules.pop(name)
    for name in _BLOCKED_MODULES:
        sys.modules[name] = None  # None in sys.modules makes import raise ImportError
    return saved


def _restore_modules(saved: dict[str, Any]) -> None:
    for name in _BLOCKED_MODULES:
        sys.modules.pop(name, None)
    for name, module in saved.items():
        sys.modules[name] = module


async def _expect_schedule_cancel(
    service: Any, stub: ExecutionDriver, request_cls: Any, error_cls: type[Exception]
) -> None:
    task = asyncio.create_task(service.execute(request_cls(runtime=stub, session_id=uuid.uuid4(), initial_input={})))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(error_cls):
        await task


def test_service_loads_and_drives_stub_without_pregel() -> None:
    """With Pregel unimportable, the service imports and classifies all outcomes."""
    saved = _block_pregel_imports()
    try:
        with pytest.raises(ImportError):
            importlib.import_module("hecate_runtime.pregel")

        # Both seam modules import cleanly with Pregel blocked — neither may
        # transitively import the graph mechanism.
        service_mod = importlib.import_module("hecate_runtime.execution_service")
        importlib.import_module("hecate_runtime.execution_driver")
        service = service_mod.runtime_execution_service
        request_cls = service_mod.RuntimeExecutionRequest
        state_cls = service_mod.RuntimeExecutionState
        decision_cls = service_mod.RuntimeEventDecision

        class _BlockedStub(ExecutionDriver):
            def __init__(self, events: list[dict[str, Any]], *, fail: bool = False, delay: float = 0) -> None:
                self._events = events
                self._fail = fail
                self._delay = delay

            async def execute(
                self,
                session_id: Any,
                initial_input: Any = None,
                execution_mode: str = "conversational",
                stream_mode: Any = None,
                resume_value: Any = None,
            ):
                for event in self._events:
                    yield event
                if self._delay:
                    await asyncio.sleep(self._delay)
                if self._fail:
                    raise RuntimeError("engine failed")

        def _blocked_request(stub: _BlockedStub, **kwargs: Any) -> Any:
            return request_cls(runtime=stub, session_id=uuid.uuid4(), initial_input={}, **kwargs)

        result = asyncio.run(service.execute(_blocked_request(_BlockedStub([{"type": "update"}, {"type": "values"}]))))
        assert result.state is state_cls.SUCCEEDED

        result = asyncio.run(service.execute(_blocked_request(_BlockedStub([], fail=True))))
        assert result.state is state_cls.FAILED
        assert result.error == "engine failed"

        result = asyncio.run(
            service.execute(
                _blocked_request(_BlockedStub([{"type": "update"}, {"type": "values"}]), should_cancel=lambda: True)
            )
        )
        assert result.state is state_cls.CANCELLED
        assert result.events == ({"type": "update"},)

        result = asyncio.run(
            service.execute(
                _blocked_request(_BlockedStub([{"type": "interrupt"}]), observer=lambda _e: decision_cls.STOP_UNKNOWN)
            )
        )
        assert result.state is state_cls.UNKNOWN

        asyncio.run(
            _expect_schedule_cancel(
                service,
                _BlockedStub([{"type": "update"}], delay=10),
                request_cls,
                service_mod.RuntimeScheduleCancelledError,
            )
        )
    finally:
        _restore_modules(saved)


# --- Driver contract: production Pregel driver and stub behave alike ---


class _EchoWorker(Worker):
    """Pass-through worker echoing the node id, mirroring test_pregel's stub."""

    async def execute(
        self, node_id: str, node_config: dict, channel_snapshot: dict, execution_context: dict | None = None
    ) -> WorkerResult:
        return WorkerResult(node_id=node_id, channel_updates={"messages": [f"{node_id}_output"]})


class _RecordingPregel(PregelRuntime):
    """Test double recording execute() kwargs — passthrough evidence only."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.calls: list[dict[str, Any]] = []

    def execute(self, *args: Any, **kwargs: Any):
        self.calls.append(kwargs)
        return super().execute(*args, **kwargs)


def _linear_graph() -> CompiledGraph:
    return CompiledGraph(
        nodes={
            "A": NodeConfig(id="A", type=NodeType.CONVERSATION, config={"model": "test", "system_prompt": "A"}),
            "B": NodeConfig(id="B", type=NodeType.CONVERSATION, config={"model": "test", "system_prompt": "B"}),
        },
        edges=[Edge(source="A", target="B"), Edge(source="B", target="__end__")],
        channels={"messages": ChannelDef(type=ChannelType.TOPIC, default=[])},
        entry_point="A",
        name="driver-contract-linear",
    )


def _make_pregel(cls: type[PregelRuntime] = PregelRuntime) -> PregelRuntime:
    return cls(
        _linear_graph(),
        _EchoWorker(),
        InMemoryCheckpointStore(),
    )


async def test_pregel_driver_forwards_events_identically() -> None:
    """The driver must not alter the event stream the service observes."""
    direct = [event async for event in _make_pregel().execute(uuid.uuid4(), initial_input={"messages": ["init"]})]
    result = await runtime_execution_service.execute(
        _request(PregelExecutionDriver(_make_pregel()), initial_input={"messages": ["init"]})
    )
    assert result.state is RuntimeExecutionState.SUCCEEDED
    assert result.events == tuple(direct)


async def test_pregel_driver_stream_closes_without_background_work() -> None:
    """Early aclose() terminates the underlying generator (no pending execution)."""
    stream = PregelExecutionDriver(_make_pregel()).execute(uuid.uuid4(), initial_input={"messages": ["init"]})
    first = await stream.__anext__()
    assert isinstance(first, dict)
    await stream.aclose()
    with pytest.raises(StopAsyncIteration):
        await stream.__anext__()
    # No orphaned tasks from the closed stream besides this test's own.
    assert [t for t in asyncio.all_tasks() if t is not asyncio.current_task()] == []


async def test_pregel_driver_passes_resume_value_opaquely() -> None:
    """The contract treats resume payloads as opaque: verbatim passthrough."""
    runtime = _make_pregel(_RecordingPregel)
    driver = PregelExecutionDriver(runtime)
    resume = {"resumed": "interrupted_attempt", "channel_state": {"messages": ["x"]}}
    stream = driver.execute(
        uuid.uuid4(),
        initial_input={"messages": ["init"]},
        execution_mode="conversational",
        resume_value=resume,
    )
    await stream.__anext__()
    await stream.aclose()
    assert runtime.calls, "driver never invoked the wrapped runtime"
    recorded = runtime.calls[0]
    assert recorded["resume_value"] is resume
    assert recorded["execution_mode"] == "conversational"


async def test_stub_driver_passes_resume_value_opaquely() -> None:
    """The stub honors the same passthrough contract (second contract impl)."""

    class _RecordingStub(_StubDriver):
        def __init__(self, events: list[dict[str, Any]]) -> None:
            super().__init__(events)
            self.calls: list[dict[str, Any]] = []

        async def execute(
            self,
            session_id: uuid.UUID,
            initial_input: dict[str, Any] | None = None,
            execution_mode: str = "conversational",
            stream_mode: Any = None,
            resume_value: Any = None,
        ):
            self.calls.append(
                {
                    "session_id": session_id,
                    "initial_input": initial_input,
                    "execution_mode": execution_mode,
                    "stream_mode": stream_mode,
                    "resume_value": resume_value,
                }
            )
            for event in self._events:
                yield event

    stub = _RecordingStub([{"type": "update"}])
    resume: dict[str, Any] = {"resumed": "interrupted_attempt"}
    stream = stub.execute(uuid.uuid4(), initial_input={"a": 1}, resume_value=resume)
    await stream.__anext__()
    assert stub.calls[0]["resume_value"] is resume
    assert stub.calls[0]["initial_input"] == {"a": 1}
