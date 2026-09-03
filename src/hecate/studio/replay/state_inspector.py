"""Time-travel state inspector.

Folds the event log up to a target version and returns the resulting channel
state snapshot plus the model-visible message list. Read-only projection that
reuses the engine's fold semantics.
"""

from __future__ import annotations

import uuid
from typing import Any

from hecate.runtime.channel import ChannelManager
from hecate.runtime.eventstore import Event, EventStore, EventType
from hecate.runtime.replay.logfold import fold_session


def _select_commit_points(events: list[Event]) -> list[int]:
    """Return the set of versions where ``STEP_END`` (or ``INTERRUPT``) anchors."""
    commit_points: set[int] = set()
    for ev in events:
        etype = ev.event_type.value if hasattr(ev.event_type, "value") else str(ev.event_type)
        if etype in {EventType.STEP_END.value, EventType.INTERRUPT.value}:
            commit_points.add(ev.version)
    return sorted(commit_points)


def _resolve_effective_version(
    events: list[Event],
    at_version: int,
) -> tuple[int, list[Event]]:
    """Choose the largest commit point <= at_version; return (effective_version, events_to_fold)."""
    commit_points = _select_commit_points(events)
    effective = 0
    for cp in commit_points:
        if cp <= at_version:
            effective = cp
        else:
            break
    if effective == 0 and at_version > 0 and commit_points:
        # Caller requested past first commit point but no commit < at_version.
        # Fall through: fold up to nothing (empty state).
        effective = 0
    sliced = [e for e in events if e.version <= effective]
    return effective, sliced


def inspect_at_version(
    events: list[Event],
    at_version: int,
) -> dict[str, Any]:
    """Fold events up to the nearest commit point and return state snapshot.

    Returns::

        {
            "effective_version": int,
            "requested_version": int,
            "channel_state": dict,        # post-fold channel snapshot
            "messages": list[Any],        # model-visible message list
            "commit_points": list[int],
            "fell_back": bool,            # True if requested version was not a commit point
        }

    Raises:
        NonReplayablePrefix: When any event lacks the current
            ``log_schema_version`` marker (propagated from ``fold_session``).
    """
    commit_points = _select_commit_points(events)
    requested = at_version
    effective, sliced = _resolve_effective_version(events, at_version)

    # Use a fresh ChannelManager + register the same channels the runtime
    # uses (the messages channel is the only one the model-visible projection
    # depends on; others remain optional).
    cm = ChannelManager()
    from hecate.runtime.types import ChannelDef, ChannelType

    cm.register("messages", ChannelDef(type=ChannelType.TOPIC, default=[]))

    # fold_session raises NonReplayablePrefix for events below current schema.
    fold_session(cm, iter(sliced))

    messages = list(cm.read("messages")) if "messages" in cm._channels else []

    return {
        "effective_version": effective,
        "requested_version": requested,
        "channel_state": cm.snapshot(),
        "messages": messages,
        "commit_points": commit_points,
        "fell_back": effective != requested,
    }


async def inspect_session_at(
    event_store: EventStore,
    session_id: uuid.UUID,
    at_version: int,
) -> dict[str, Any]:
    """Convenience wrapper: load events, dispatch to ``inspect_at_version``.

    Raises ``NonReplayablePrefix`` for sub-current schema events.
    """
    events = await event_store.get_events(session_id)
    return inspect_at_version(events, at_version)
