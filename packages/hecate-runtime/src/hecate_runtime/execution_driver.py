"""Runtime-internal execution driver seam for the shared execution service.

The shared execution application service drives one assembled execution without
knowing which mechanism produces the event stream. ``ExecutionDriver`` is the
minimal contract covering exactly that consumed face; the built-in Agent
Controller (pending delivery) and the Workflow/Pregel driver are its consumers.
This seam is internal to the runtime domain — it is deliberately not an
external-runtime plugin framework. ``resume_value`` is opaque here: each driver
interprets its own recovery semantics (the Pregel driver restores channel
state from it).
"""

from __future__ import annotations

import abc
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from hecate_runtime.types import StreamMode

if TYPE_CHECKING:
    from hecate_runtime.pregel import PregelRuntime


class ExecutionDriver(abc.ABC):
    """Minimal execution face consumed by the shared execution service.

    Implementations return an async event iterator for one execution. The
    iterator must support early ``aclose()``: the service closes the stream on
    unknown-outcome stops and cooperative cancellation, and a closed stream
    must not leave background execution behind. ``resume_value`` is an opaque
    continuation payload whose interpretation belongs to the concrete driver.
    """

    @abc.abstractmethod
    def execute(
        self,
        session_id: uuid.UUID,
        initial_input: dict[str, Any] | None = None,
        execution_mode: str = "conversational",
        stream_mode: StreamMode = StreamMode.VALUES,
        resume_value: Any = None,
    ) -> AsyncIterator[Any]:
        """Yield engine events for one execution of the assembled mechanism."""


class PregelExecutionDriver(ExecutionDriver):
    """Drive an assembled Pregel graph runtime and forward its event stream.

    Composition (not inheritance) keeps the driver's public face limited to
    the contract; the wrapped runtime is passed through unwrapped so the
    service's early ``aclose()`` reaches the engine's cleanup untouched.
    """

    def __init__(self, runtime: PregelRuntime) -> None:
        self._runtime = runtime

    def execute(
        self,
        session_id: uuid.UUID,
        initial_input: dict[str, Any] | None = None,
        execution_mode: str = "conversational",
        stream_mode: StreamMode = StreamMode.VALUES,
        resume_value: Any = None,
    ) -> AsyncIterator[Any]:
        return self._runtime.execute(
            session_id=session_id,
            initial_input=initial_input,
            stream_mode=stream_mode,
            resume_value=resume_value,
            execution_mode=execution_mode,
        )
