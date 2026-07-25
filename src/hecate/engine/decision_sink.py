"""Engine-layer decision emission infrastructure.

Provides the ``DecisionSink`` ABC and ``ToolDecisionEmitter`` for emitting
structured tool policy decision events from the engine layer without depending
on ``models/`` or ``services/``.

Design (see openspec/changes/siem-security-pipeline/design.md D1):
- ``DecisionSink`` is the engine-layer interface (like ``EnginePort``).
- ``ToolDecisionEmitter`` buffers events in memory; a services-layer
  ``ToolDecisionService`` implements ``DecisionSink`` and flushes to the
  database via async batch writing.
- If no sink is registered, ``emit()`` is a no-op (backward compatible).
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)


class DecisionSink(ABC):
    """Abstract sink for tool policy decision events.

    Implemented by ``ToolDecisionService`` in the services layer.
    The engine calls ``emit()`` synchronously; the implementation is
    responsible for buffering and async flushing.
    """

    @abstractmethod
    def emit(self, event: dict[str, Any]) -> None:
        """Accept a security audit event for storage.

        This method MUST be non-blocking — it should buffer the event
        and return immediately. Actual database writes happen in a
        background flush cycle.

        Args:
            event: Audit event dictionary with keys: agent_id,
                workspace_id, session_id, tool_name, arguments_hash,
                decision, reason, policy_version, on_behalf_of_user,
                layer_results.
        """
        ...


class NullDecisionSink(DecisionSink):
    """No-op decision sink — default when no service is registered."""

    def emit(self, event: dict[str, Any]) -> None:
        """Discard the event silently."""
        pass


class ToolDecisionEmitter:
    """Thread-safe emitter that buffers events and delegates to a sink.

    Usage:
        # At startup (services layer):
        decision_emitter.set_sink(ToolDecisionService(...))

        # In engine code:
        decision_emitter.emit({...})

    If no sink is set, ``emit()`` is a no-op.
    """

    def __init__(self, buffer_size: int = 10000) -> None:
        self._sink: DecisionSink = NullDecisionSink()
        self._buffer: deque[dict[str, Any]] = deque(maxlen=buffer_size)
        self._lock = threading.Lock()
        self._enabled = False

    @property
    def enabled(self) -> bool:
        """Whether audit emission is active."""
        return self._enabled

    def set_sink(self, sink: DecisionSink) -> None:
        """Register a concrete decision sink (called by services layer at startup).

        Args:
            sink: The decision sink implementation.
        """
        with self._lock:
            self._sink = sink
            self._enabled = True

    def disable(self) -> None:
        """Disable audit emission (events become no-op)."""
        with self._lock:
            self._sink = NullDecisionSink()
            self._enabled = False

    def emit(self, event: dict[str, Any]) -> None:
        """Emit a security audit event.

        Non-blocking: delegates to the registered sink's ``emit()``.
        If no sink is registered, this is a no-op.

        Args:
            event: Audit event dictionary.
        """
        if not self._enabled:
            return
        try:
            self._sink.emit(event)
        except Exception:
            logger.warning("Audit emission failed", exc_info=True)

    def build_event(
        self,
        *,
        agent_id: str | None,
        workspace_id: str | None,
        tool_name: str,
        decision: str,
        reason: str = "",
        arguments_hash: str = "",
        policy_version: str = "",
        session_id: str | None = None,
        on_behalf_of_user: str | None = None,
        layer_results: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Build a standard audit event dictionary.

        Args:
            agent_id: Agent performing the action.
            workspace_id: Workspace scope.
            tool_name: Tool being evaluated.
            decision: Final policy decision.
            reason: Human-readable explanation.
            arguments_hash: SHA-256 hash of tool arguments.
            policy_version: Hash of effective policy config.
            session_id: Session scope (optional).
            on_behalf_of_user: User the agent acts for (optional).
            layer_results: Per-layer decision breakdown.

        Returns:
            Event dictionary ready for ``emit()``.
        """
        return {
            "agent_id": agent_id or "",
            "workspace_id": workspace_id or "",
            "session_id": session_id,
            "tool_name": tool_name,
            "decision": decision,
            "reason": reason,
            "arguments_hash": arguments_hash,
            "policy_version": policy_version,
            "on_behalf_of_user": on_behalf_of_user,
            "layer_results": layer_results or [],
        }


# Module-level singleton — engine code imports this directly.
decision_emitter = ToolDecisionEmitter()
