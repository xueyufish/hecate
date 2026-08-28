"""Channel-level log emission policy (default-in + blacklist).

The engine logs channel writes by default. Three classes of channels are
excluded by a single source of truth here so that:

1. New value channels forgotten by contributors are logged (over-log, not lost).
2. Recoverable control channels are not duplicated into the log.
3. Channels persisted through dedicated stores are not double-bookkept.

Boundary equivalent assertions (see ``engine/invariants.py``) only cover
non-excluded channels.
"""

from __future__ import annotations

_EPHEMERAL: frozenset[str] = frozenset(
    {
        "_fanout__resolved",  # not used as a literal key; defensive placeholder
        "_resume_value",
    }
)


def _is_fanout_subchannel(name: str) -> bool:
    """Fan-out branch sub-channels registered dynamically by PregelRuntime."""
    return name.startswith("_fanout__")


def _is_underscore_control(name: str) -> bool:
    """Underscore-prefixed channels are injected per-request by execution_service."""
    return name.startswith("_")


def _is_sys_control(name: str) -> bool:
    return name.startswith("sys.")


def should_log_channel(name: str) -> bool:
    """Return True if a write to the named channel should produce a CHANNEL_WRITE event.

    Excluded classes:
      * ephemeral structural intermediates (``_fanout__*``, ``_resume_value``)
      * underscore-prefixed control channels (re-injected at restore by services)
      * ``sys.``-prefixed control channels (engine-controlled, regenerated per request)

    Special case: ``_route`` is explicitly NOT excluded — condition-edge routing
    is part of fold correctness and must survive replay.
    """
    if name == "_route":
        return True
    if name in _EPHEMERAL:
        return False
    if _is_fanout_subchannel(name):
        return False
    return not (_is_underscore_control(name) or _is_sys_control(name))
