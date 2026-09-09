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

from hecate.runtime.channel import ChannelManager
from hecate.runtime.eventstore import CURRENT_LOG_SCHEMA_VERSION, Event, EventStore

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
        :class:`NonReplayablePrefix` when a **state-carrying** event
        (``CHANNEL_WRITE`` / ``EVICTION`` / ``FORK``) lacks the current
        ``log_schema_version`` marker — the marker guards replayability of
        recorded values. Bookkeeping events the fold ignores (``CUSTOM``,
        ``NODE_START``/``NODE_END``, ``STEP_END``, …) are skipped regardless
        of marker presence, so full-log folds over real engine streams work.

    Channel writes go through ``channel_manager.write`` so they use the
        registered ``ChannelBehavior.write`` — the same fold function as live
        mutation. This prevents projection drift between live and replay paths.
    """
    last_version = 0
    for event in events:
        etype = event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)

        state_carrying = etype in ("CHANNEL_WRITE", "EVICTION", "FORK")
        if state_carrying and event.payload.get("log_schema_version") != CURRENT_LOG_SCHEMA_VERSION:
            raise NonReplayablePrefixError(event.session_id, event.version)

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
        elif etype == "FORK":
            # 1.3.21② fork bootstrap: hydrate the snapshot wholesale (restore
            # semantics, not write semantics). The snapshot is the child
            # session's source of truth for the inherited prefix — later
            # CHANNEL_WRITE events apply incrementally on top of it. Defensive
            # filter: only channels the log could carry survive hydration.
            from hecate.runtime.replay.logpolicy import should_log_channel

            state = event.payload.get("channel_state") or {}
            hydratable = {k: v for k, v in state.items() if should_log_channel(k)}
            channel_manager.restore(hydratable)
        elif etype == "STEP_END":
            # 1.3.21③ T2b: STEP_END carries a ``fanout`` segment with branch
            # sub-channel values. Replay rebuilds the sub-channels here so
            # log-only recovery and fork payloads preserve branch outputs.
            fanout_segments = (event.payload or {}).get("fanout")
            if fanout_segments:
                _apply_fanout_segments(channel_manager, fanout_segments)

        last_version = event.version

    return last_version


def _apply_fanout_segments(channel_manager: ChannelManager, segments: list) -> None:
    """Rehydrate ``_fanout__*`` sub-channels from a STEP_END ``fanout`` segment.

    The segment was emitted by ``PregelRuntime._build_fanout_commit_payload``
    right before the STEP_END commit. Sub-channel names are constructed as
    ``_fanout__{source}__{suffix}`` where ``suffix`` is the branch ID
    (static) or ``idx{i}`` (dynamic).
    """
    for segment in segments:
        source = segment.get("source", "")
        sub_channels = segment.get("sub_channels") or {}
        for suffix, value in sub_channels.items():
            name = f"_fanout__{source}__{suffix}"
            channel_manager.restore({name: value})


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
