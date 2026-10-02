"""Session-scoped monotonic-denial tracker (T3.3).

A denied tool call is recorded by ``tool_call_id``. Subsequent evaluations
of the same call (within the same session) short-circuit to DENY without
re-running the policy / approval machinery — denying a denial is a bug,
resurrecting it is also a bug. Together with the runtime state, the
``MONOTONIC.DENIAL`` log invariant (T3.6) verifies the same property at
restore / replay time.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class MonotonicDenialTracker:
    """Per-session set of denied ``tool_call_id``s."""

    _denied: set[str] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def deny(self, tool_call_id: str) -> None:
        """Record a denial. Idempotent — repeated calls are no-ops."""
        if not tool_call_id:
            return
        with self._lock:
            self._denied.add(tool_call_id)

    def is_denied(self, tool_call_id: str) -> bool:
        """Return True if the call has been denied in this session."""
        if not tool_call_id:
            return False
        with self._lock:
            return tool_call_id in self._denied

    def clear(self) -> None:
        """Reset the tracker (testing utility)."""
        with self._lock:
            self._denied.clear()


__all__ = ["MonotonicDenialTracker"]
