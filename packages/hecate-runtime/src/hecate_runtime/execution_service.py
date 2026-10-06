"""Shared execution application service over an assembled Pregel runtime.

The service owns only the execution-driving behavior shared by platform and
standalone adapters: event capture, observer decisions, cooperative cancel
checks, and honest outcome classification. It deliberately owns no identity,
authorization, database, Task/Run projection, evidence, or transport state.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from typing import Any

from hecate_runtime.pregel import PregelRuntime
from hecate_runtime.types import StreamMode


class RuntimeExecutionState(StrEnum):
    """Adapter-neutral outcome of one runtime invocation."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class RuntimeEventDecision(Enum):
    """What the adapter wants the service to do after observing an event."""

    CONTINUE = "continue"
    STOP_UNKNOWN = "stop_unknown"


class CooperativeCancellationError(Exception):
    """A caller-approved cancel reached the next execution boundary."""


class RuntimeScheduleCancelledError(Exception):
    """Carries the partial result when the surrounding task is cancelled."""

    def __init__(self, result: RuntimeExecutionResult) -> None:
        super().__init__("runtime schedule cancelled before completion")
        self.result = result


@dataclass(frozen=True)
class RuntimeExecutionRequest:
    """One invocation of an already-assembled runtime."""

    runtime: PregelRuntime
    session_id: uuid.UUID
    initial_input: dict[str, Any]
    execution_mode: str = "conversational"
    stream_mode: StreamMode = StreamMode.VALUES
    observer: Callable[[Any], RuntimeEventDecision] | None = None
    should_cancel: Callable[[], bool] = lambda: False


@dataclass(frozen=True)
class RuntimeExecutionResult:
    """Captured events and the shared outcome classification."""

    state: RuntimeExecutionState
    events: tuple[Any, ...] = ()
    error: str | None = None
    cancel_requested: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.state is RuntimeExecutionState.SUCCEEDED


class RuntimeExecutionService:
    """Drive one runtime invocation without owning adapter persistence."""

    async def execute(self, request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        events: list[Any] = []
        cancel_requested = False
        try:
            stream = request.runtime.execute(
                session_id=request.session_id,
                initial_input=request.initial_input,
                stream_mode=request.stream_mode,
                execution_mode=request.execution_mode,
            )
            try:
                async for event in stream:
                    events.append(event)
                    if request.observer is not None and request.observer(event) is RuntimeEventDecision.STOP_UNKNOWN:
                        # Close the generator NOW, on this task and in this
                        # context: leaving it to GC would run the runtime's
                        # span/OTel cleanup under whichever task collects it,
                        # detaching context tokens across contexts.
                        await stream.aclose()
                        return RuntimeExecutionResult(
                            state=RuntimeExecutionState.UNKNOWN,
                            events=tuple(events),
                            detail={"reason": "observer requested unknown stop"},
                        )
                    if request.should_cancel():
                        cancel_requested = True
                        raise CooperativeCancellationError
            finally:
                # The with-block body must not leave the stream unclosed on
                # any other early exit either (cancel, exceptions).
                with contextlib.suppress(Exception):
                    await stream.aclose()
                if request.should_cancel():
                    cancel_requested = True
                    raise CooperativeCancellationError
            return RuntimeExecutionResult(
                state=RuntimeExecutionState.CANCELLED if cancel_requested else RuntimeExecutionState.SUCCEEDED,
                events=tuple(events),
                cancel_requested=cancel_requested,
            )
        except CooperativeCancellationError:
            return RuntimeExecutionResult(
                state=RuntimeExecutionState.CANCELLED,
                events=tuple(events),
                cancel_requested=True,
            )
        except asyncio.CancelledError:
            raise RuntimeScheduleCancelledError(
                RuntimeExecutionResult(
                    state=RuntimeExecutionState.UNKNOWN,
                    events=tuple(events),
                    error="runtime schedule cancelled before completion",
                    detail={"reason": "schedule cancelled before completion"},
                )
            ) from None
        except Exception as exc:  # noqa: BLE001 - run failure must become a run fact
            return RuntimeExecutionResult(
                state=RuntimeExecutionState.FAILED,
                events=tuple(events),
                error=str(exc),
            )


runtime_execution_service = RuntimeExecutionService()
