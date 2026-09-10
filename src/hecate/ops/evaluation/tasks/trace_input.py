"""Project a scored ``EvalInput`` from a session's event log.

The online worker decides *what* to score (a completed root trace) and
*when* it happened (the trace's time window); this module answers *what
happened*: it replays the session's events and projects the messages and
tool calls inside the window into an :class:`EvalInput`.

Message derivation reuses ``derive_session_messages`` (the same read-side
projection the conversation API uses — ``LLM_REQUEST`` payloads carry the
per-turn message list, ``CHANNEL_WRITE`` events on the ``messages`` channel
carry incremental additions); windowing then slices those messages by the
trace's ``start_time`` / ``end_time``. Tool calls come from ``TOOL_CALL``
events in the window (payload keys: ``tool_name`` / ``arguments`` /
``tool_call_id``).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from hecate.ops.evaluation.types import EvalInput
from hecate.runtime.eventstore import EventStore, EventType

logger = logging.getLogger(__name__)

_DEFAULT_MAX_WINDOW_MESSAGES = 200


async def build_eval_input(
    session_id: uuid.UUID,
    window_start: datetime,
    window_end: datetime | None,
    event_store: EventStore,
    max_messages: int = _DEFAULT_MAX_WINDOW_MESSAGES,
) -> EvalInput | None:
    """Build an :class:`EvalInput` from the events inside a trace's window.

    Args:
        session_id: The trace's session — the event-log partition to replay.
        window_start: The root trace's ``start_time`` (inclusive).
        window_end: The root trace's ``end_time`` (inclusive); ``None``
            scores the whole session so far.
        event_store: The process EventStore (in-memory or PostgreSQL).
        max_messages: Upper bound on messages projected from the window;
            oversize windows are rejected (logged) rather than truncated
            mid-conversation, so samples stay semantically complete.

    Returns:
        The assembled input, or ``None`` when the window holds no complete
        user→assistant exchange (nothing to score) — the caller logs and
        skips the sample.
    """
    messages = await _windowed_messages(session_id, window_start, window_end, event_store)
    if len(messages) > max_messages:
        logger.warning(
            "Session %s window [%s, %s] has %d messages (cap %d) — skipping sample",
            session_id,
            window_start,
            window_end,
            len(messages),
            max_messages,
        )
        return None

    query, generated_answer, history = _split_last_exchange(messages)
    if query is None or generated_answer is None:
        logger.debug(
            "Session %s window has no complete user→assistant exchange — skipping",
            session_id,
        )
        return None

    return EvalInput(
        query=query,
        generated_answer=generated_answer,
        conversation_history=history or None,
        tool_calls=await _windowed_tool_calls(session_id, window_start, window_end, event_store),
    )


async def _load_events(session_id: uuid.UUID, event_store: EventStore) -> list[Any]:
    """Replay a session's events, tolerating list- and iterator-shaped stores."""
    raw = await event_store.get_events(session_id=session_id)
    return [ev async for ev in raw] if hasattr(raw, "__aiter__") else list(raw)


async def _windowed_messages(
    session_id: uuid.UUID,
    window_start: datetime,
    window_end: datetime | None,
    event_store: EventStore,
) -> list[dict[str, Any]]:
    """Project user/assistant messages from the window, in causal order.

    Reuses ``derive_session_messages`` (function-level cross-domain import,
    per the studio-layering convention) and slices by ``created_at`` —
    the derivation already dedupes and orders by ``(superstep, version)``.
    """
    from hecate.studio.replay.assembler import derive_session_messages

    all_messages = await derive_session_messages(session_id, event_store)

    windowed: list[dict[str, Any]] = []
    start = _aware(window_start)
    end = _aware(window_end) if window_end is not None else None
    for message in all_messages:
        created_at = _parse_timestamp(message.get("created_at"))
        if created_at is None:
            continue
        created_at = _aware(created_at)
        if created_at < start:
            continue
        if end is not None and created_at > end:
            continue
        if message.get("role") in ("user", "assistant") and message.get("content") is not None:
            windowed.append({"role": message["role"], "content": message["content"]})
    return windowed


async def _windowed_tool_calls(
    session_id: uuid.UUID,
    window_start: datetime,
    window_end: datetime | None,
    event_store: EventStore,
) -> list[dict[str, Any]] | None:
    """Collect ``TOOL_CALL`` events inside the window as EvalInput tool_calls."""
    events = await _load_events(session_id, event_store)
    tool_calls: list[dict[str, Any]] = []
    for event in events:
        if _event_type(event) != EventType.TOOL_CALL.value:
            continue
        if not _in_window(event.timestamp, window_start, window_end):
            continue
        payload = event.payload or {}
        tool_calls.append(
            {
                "name": payload.get("tool_name"),
                "args": payload.get("arguments"),
                "tool_call_id": payload.get("tool_call_id"),
            }
        )
    return tool_calls or None


def _split_last_exchange(
    messages: list[dict[str, Any]],
) -> tuple[str | None, str | None, list[dict[str, Any]]]:
    """Split the window's messages into (query, generated_answer, history).

    The query is the last user message before the final assistant message;
    history is everything before that query.
    """
    last_assistant = None
    for index in range(len(messages) - 1, -1, -1):
        if messages[index]["role"] == "assistant":
            last_assistant = index
            break
    if last_assistant is None or last_assistant == 0:
        return None, None, []

    last_user = None
    for index in range(last_assistant - 1, -1, -1):
        if messages[index]["role"] == "user":
            last_user = index
            break
    if last_user is None:
        return None, None, []

    query = _content_to_str(messages[last_user]["content"])
    answer = _content_to_str(messages[last_assistant]["content"])
    if not query or not answer:
        return None, None, []
    history = messages[:last_user]
    return query, answer, history


def _content_to_str(content: Any) -> str:
    if isinstance(content, str):
        return content
    return str(content).strip() if content is not None else ""


def _event_type(event: Any) -> str:
    return event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)


def _in_window(timestamp: datetime, window_start: datetime, window_end: datetime | None) -> bool:
    timestamp = _aware(timestamp)
    start = _aware(window_start)
    if timestamp < start:
        return False
    return window_end is None or timestamp <= _aware(window_end)


def _aware(dt: datetime) -> datetime:
    """Treat naive datetimes as UTC (SQLite drops ``DateTime(timezone=True)`` offsets)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _parse_timestamp(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None
    return None
