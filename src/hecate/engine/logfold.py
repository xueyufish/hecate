"""Fold machine — rebuild channel state from the event log.

The fold function is the SAME ``ChannelBehavior.write`` used for live
mutation. Two implications:

1. Live writes and replay go through the same code path; they cannot diverge.
2. Inputs MUST be post-adjudication values (already filtered through
   ConflictResolver). The fold does not re-resolve; it accepts only the
   values that were actually applied at write time.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from hecate.engine.channel import ChannelManager
from hecate.engine.eventstore import CURRENT_LOG_SCHEMA_VERSION, Event, EventStore

if TYPE_CHECKING:
    from collections.abc import Iterable


class NonReplayablePrefixError(Exception):
    """Raised when fold encounters an event whose schema version is below current.

    Treats such events as a non-replayable prefix; callers can fall back to
    a snapshot-only restore path.
    """

    def __init__(self, session_id: uuid.UUID, stopped_at_version: int) -> None:
        self.session_id = session_id
        self.stopped_at_version = stopped_at_version
        super().__init__(
            f"Non-replayable event prefix for session_id={session_id}; stopped at version={stopped_at_version}"
        )


NonReplayablePrefix = NonReplayablePrefixError  # backward-compat alias


def fold_session(
    channel_manager: ChannelManager,
    events: Iterable[Event],
) -> int:
    """Apply events sequentially to ``channel_manager``.

    Returns the version at which folding stopped. Raises
    :class:`NonReplayablePrefix` when an event lacks the current
    ``log_schema_version`` marker.

    Channel writes go through ``channel_manager.write`` so they use the
    registered ``ChannelBehavior.write`` — the same fold function as live
    mutation. This prevents projection drift between live and replay paths.
    """
    last_version = 0
    for event in events:
        if event.payload.get("log_schema_version") != CURRENT_LOG_SCHEMA_VERSION:
            raise NonReplayablePrefix(event.session_id, event.version)

        etype = event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)

        if etype == "CHANNEL_WRITE":
            channel_manager.write(event.payload["channel"], event.payload["value"])
        elif etype == "CHANNEL_WRITE_REJECTED":
            pass
        elif etype == "EVICTION":
            ch = event.payload["channel"]
            current = channel_manager.read(ch)
            if isinstance(current, list) and event.payload.get("drop_indices"):
                remaining = [item for idx, item in enumerate(current) if idx not in set(event.payload["drop_indices"])]
            else:
                remaining = current
            channel_manager.restore({ch: remaining})

        last_version = event.version

    return last_version


async def fold_session_from_store(
    channel_manager: ChannelManager,
    event_store: EventStore,
    session_id: uuid.UUID,
) -> int:
    """Fetch events from ``event_store`` and fold into ``channel_manager``."""
    events = await event_store.get_events(session_id)
    return fold_session(channel_manager, iter(events))


def derive_messages(channel_manager: ChannelManager) -> list[Any]:
    """Return the model-visible message history projection for the current state.

    Reads the ``messages`` channel via the registered behavior — same code
    path as live execution, no separate serialization.
    """
    try:
        return list(channel_manager.read("messages"))
    except KeyError:
        return []
