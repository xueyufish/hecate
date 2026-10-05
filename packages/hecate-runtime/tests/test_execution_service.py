"""Shared runtime execution-service behavior."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from hecate_runtime.execution_service import (
    RuntimeEventDecision,
    RuntimeExecutionRequest,
    RuntimeExecutionState,
    RuntimeScheduleCancelledError,
    runtime_execution_service,
)


class _Runtime:
    def __init__(self, events: list[dict[str, Any]], *, fail: bool = False, delay_after_events: float = 0) -> None:
        self._events = events
        self._fail = fail
        self._delay_after_events = delay_after_events

    async def execute(self, **_: Any):
        for event in self._events:
            yield event
            if self._delay_after_events:
                await asyncio.sleep(self._delay_after_events)
        if self._fail:
            raise RuntimeError("engine failed")


def _request(runtime: _Runtime, **kwargs: Any) -> RuntimeExecutionRequest:
    from uuid import uuid4

    return RuntimeExecutionRequest(
        runtime=runtime,
        session_id=uuid4(),
        initial_input={},
        **kwargs,
    )


async def test_successful_run_captures_events() -> None:
    observed: list[Any] = []

    def observe(event: Any) -> RuntimeEventDecision:
        observed.append(event)
        return RuntimeEventDecision.CONTINUE

    result = await runtime_execution_service.execute(
        _request(
            _Runtime([{"type": "update", "value": 1}, {"type": "values", "value": 2}]),
            observer=observe,
        )
    )
    assert result.state is RuntimeExecutionState.SUCCEEDED
    assert result.events == tuple(observed)


async def test_engine_failure_is_run_fact() -> None:
    result = await runtime_execution_service.execute(_request(_Runtime([], fail=True)))
    assert result.state is RuntimeExecutionState.FAILED
    assert result.error == "engine failed"


async def test_cooperative_cancel_stops_after_observed_event() -> None:
    result = await runtime_execution_service.execute(
        _request(
            _Runtime([{"type": "update"}, {"type": "values"}]),
            should_cancel=lambda: True,
        )
    )
    assert result.state is RuntimeExecutionState.CANCELLED
    assert result.cancel_requested is True
    assert result.events == ({"type": "update"},)


async def test_observer_can_stop_as_unknown() -> None:
    result = await runtime_execution_service.execute(
        _request(
            _Runtime([{"type": "interrupt"}, {"type": "values"}]),
            observer=lambda _event: RuntimeEventDecision.STOP_UNKNOWN,
        )
    )
    assert result.state is RuntimeExecutionState.UNKNOWN
    assert result.detail["reason"] == "observer requested unknown stop"


async def test_schedule_cancel_returns_unknown_partial_result() -> None:
    task = asyncio.create_task(
        runtime_execution_service.execute(_request(_Runtime([{"type": "update"}], delay_after_events=10)))
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(RuntimeScheduleCancelledError):
        await task
