"""Replay timeline assembler.

Reads the event log for a session and partitions events into trace segments.
Pure read-side projection — no writes to the log.

Public surface:
- REPLAY_PAYLOAD_PREVIEW_CHARS: payload summary length.
- assemble_timeline(events, *, detail=False, from_version=0, limit=100)
- enrich_traces(events, db) -> dict[trace_id, TraceMetadata]
- derive_session_messages(session_id, event_store) -> list[dict] (A2 closure)
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from hecate.runtime.eventstore import Event, EventType

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


REPLAY_PAYLOAD_PREVIEW_CHARS: int = 200
"""Truncate payload summaries to this many characters (non-string values JSON-serialized first)."""

_REPLAY_TIMELINE_DEFAULT_LIMIT: int = 100
_REPLAY_TIMELINE_MAX_LIMIT: int = 500

_UNATTRIBUTED_KEY: str = "__unattributed__"
"""Sentinel trace_id for events without trace correlation (None or all-zero)."""

_GUARDRAIL_BLOCK_PREFIXES: tuple[str, ...] = (
    "Tool blocked: ",
    "Tool denied by access policy",
)
"""Prefixes in tool-role error messages that mark guardrail blocks (Phase 1 derivation)."""

_PAYLOAD_TRUNCATED_KEYS: tuple[str, ...] = (
    "messages",
    "value",
    "arguments",
    "response",
    "result",
    "tools",
)
"""Payload keys whose contents are truncated to summary in non-detail responses."""


def _is_attributed(trace_id: str | None) -> bool:
    """True iff trace_id is a non-degenerate correlation value."""
    if trace_id is None:
        return False
    return trace_id != "0" * 32


def _summary_value(value: Any, *, detail: bool) -> tuple[Any, bool]:
    """Build (preview, truncated_flag) for a payload value.

    Non-string values are JSON-serialized first. Strings longer than the
    preview constant are truncated; other types are returned as-is with
    truncated=False.
    """
    if detail:
        return value, False
    if isinstance(value, str):
        if len(value) <= REPLAY_PAYLOAD_PREVIEW_CHARS:
            return value, False
        return value[:REPLAY_PAYLOAD_PREVIEW_CHARS], True
    try:
        serialized = json.dumps(value, default=str)
    except (TypeError, ValueError):
        return repr(value), False
    if len(serialized) <= REPLAY_PAYLOAD_PREVIEW_CHARS:
        return value, False
    return serialized[:REPLAY_PAYLOAD_PREVIEW_CHARS], True


def _summarize_payload(payload: dict[str, Any], *, detail: bool) -> tuple[dict[str, Any], bool]:
    """Return (summary_payload, truncated_flag).

    Lists-of-keys ``_PAYLOAD_TRUNCATED_KEYS`` are previewed; other keys are
    preserved unchanged. The flag is True iff any preview was truncated.
    """
    summary: dict[str, Any] = {}
    truncated = False
    for k, v in payload.items():
        if k in _PAYLOAD_TRUNCATED_KEYS:
            preview, was_truncated = _summary_value(v, detail=detail)
            summary[k] = preview
            if was_truncated:
                truncated = True
        else:
            summary[k] = v
    return summary, truncated


def assemble_timeline(
    events: list[Event],
    *,
    detail: bool = False,
    from_version: int = 0,
    limit: int = _REPLAY_TIMELINE_DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Partition events into trace segments and apply payload summarization.

    Args:
        events: Version-ascending event list (typically from
            ``EventStore.get_events(session_id, from_version)``).
        detail: When True, return full payload (no truncation).
        from_version: Minimum event version to include (inclusive).
        limit: Maximum number of events to include across all segments.

    Returns:
        Dict with shape::

            {
                "traces": [
                    {"trace_id": str, "event_count": int,
                     "first_version": int, "events": [...]},
                    ...
                ],
                "unattributed": [...],   # events with no trace correlation
                "next_cursor": int | None  # version for the next page (None = exhausted)
            }
    """
    bounded_limit = max(1, min(limit, _REPLAY_TIMELINE_MAX_LIMIT))
    sliced = [e for e in events if e.version >= from_version][:bounded_limit]

    segments: dict[str, dict[str, Any]] = {}
    unattributed: list[dict[str, Any]] = []
    last_version: int | None = None
    overall_truncated = False

    for event in sliced:
        summary_payload, truncated = _summarize_payload(event.payload or {}, detail=detail)
        if truncated:
            overall_truncated = True
        record = {
            "event_type": event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type),
            "superstep": event.superstep,
            "node_id": event.node_id,
            "timestamp": event.timestamp.isoformat() if hasattr(event.timestamp, "isoformat") else event.timestamp,
            "version": event.version,
            "payload": summary_payload,
        }

        if not _is_attributed(event.trace_id):
            unattributed.append(record)
        else:
            seg = segments.setdefault(
                event.trace_id,
                {"trace_id": event.trace_id, "event_count": 0, "first_version": event.version, "events": []},
            )
            seg["event_count"] += 1
            seg["events"].append(record)

        last_version = event.version

    # Stable segment order by first_version.
    ordered_traces = sorted(segments.values(), key=lambda s: s["first_version"])

    next_cursor = int(last_version) + 1 if last_version is not None and len(sliced) == bounded_limit else None

    return {
        "traces": ordered_traces,
        "unattributed": unattributed,
        "next_cursor": next_cursor,
        "payload_truncated": overall_truncated,
    }


def derive_message_bodies(
    events: list[Event],
    trace_ids: list[str],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Associate LLM_RESPONSE / TOOL_RESULT events with subsequent messages writes.

    Returns a mapping keyed by ``(trace_id, event_id_or_type)`` (using event
    ``id`` for unique correlation). For each LLM_RESPONSE / TOOL_RESULT, the
    corresponding assistant or tool message in the same node execution window
    is appended under the same key.

    Read-side projection only; does not mutate events.
    """
    # Build per-trace view, preserving order.
    by_trace: dict[str, list[Event]] = {}
    for ev in events:
        if not _is_attributed(ev.trace_id):
            continue
        by_trace.setdefault(ev.trace_id, []).append(ev)

    result: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for trace_id in trace_ids:
        trace_events = by_trace.get(trace_id, [])
        for i, ev in enumerate(trace_events):
            etype = ev.event_type.value if hasattr(ev.event_type, "value") else str(ev.event_type)
            if etype not in {EventType.LLM_RESPONSE.value, EventType.TOOL_RESULT.value}:
                continue
            # Look forward for messages-channel writes within the same node window.
            # Window is bounded by the next NODE_END for the same node_id, or
            # the next different-node event.
            same_node = ev.node_id
            body_messages: list[dict[str, Any]] = []
            for later in trace_events[i + 1 :]:
                if later.node_id != same_node:
                    break
                ltype = later.event_type.value if hasattr(later.event_type, "value") else str(later.event_type)
                if ltype == EventType.CHANNEL_WRITE.value:
                    payload = later.payload or {}
                    if payload.get("channel") == "messages":
                        body_messages.append(payload.get("value"))
            result[(trace_id, str(ev.id))] = body_messages

    return result


def derive_guardrail_blocks(events: list[Event]) -> list[dict[str, Any]]:
    """Derive guardrail blocks from synthetic tool-role error messages.

    Phase 1: scan CHANNEL_WRITE events on the ``messages`` channel; any tool
    message whose content matches a known block prefix becomes a guardrail
    entry. E3 stage events supersede this when shipped.
    """
    entries: list[dict[str, Any]] = []
    for ev in events:
        etype = ev.event_type.value if hasattr(ev.event_type, "value") else str(ev.event_type)
        if etype != EventType.CHANNEL_WRITE.value:
            continue
        payload = ev.payload or {}
        if payload.get("channel") != "messages":
            continue
        value = payload.get("value")
        if not isinstance(value, dict):
            continue
        if not value.get("is_error"):
            continue
        content = value.get("content")
        if not isinstance(content, str):
            continue
        for prefix in _GUARDRAIL_BLOCK_PREFIXES:
            if content.startswith(prefix):
                entries.append(
                    {
                        "version": ev.version,
                        "node_id": ev.node_id,
                        "superstep": ev.superstep,
                        "reason": content[len(prefix) :].strip() if content.startswith(prefix) else content,
                        "block_type": prefix.rstrip(": ").lower(),
                    }
                )
                break
    return entries


async def enrich_traces(
    events: list[Event],
    db: AsyncSession,
) -> dict[str, dict[str, Any]]:
    """Look up OTel TraceModel records by trace_id for timing/usage enrichment.

    Match key is ``TraceModel.metadata_["otel.trace_id"]`` — the hex string
    recorded by the OTel span processor. Session-scoped on the client side.

    Returns an empty dict on miss (caller renders without timing/usage).
    """
    from sqlalchemy import select

    from hecate.models.trace import TraceModel

    trace_ids_set = {e.trace_id for e in events if _is_attributed(e.trace_id)}
    trace_ids = sorted(tid for tid in trace_ids_set if tid is not None)
    if not trace_ids:
        return {}

    result: dict[str, dict[str, Any]] = {}
    # TraceModel.metadata_ is a JSON column; query by JSON-path in Python.
    rows = await db.execute(select(TraceModel))
    for row in rows.scalars():
        md = row.metadata_ or {}
        otel_trace_id = md.get("otel.trace_id")
        if otel_trace_id in trace_ids:
            total_latency_ms = None
            output_data = row.output_data or {}
            if isinstance(output_data, dict):
                total = output_data.get("total_latency_ms")
                if isinstance(total, int | float):
                    total_latency_ms = total
            ttft_ms = output_data.get("ttft_ms") if isinstance(output_data, dict) else None
            result[otel_trace_id] = {
                "status": row.status,
                "usage": row.usage,
                "total_latency_ms": total_latency_ms,
                "ttft_ms": ttft_ms,
                "span_name": row.name,
            }
    return result


async def derive_session_messages(session_id: uuid.UUID, event_store: Any) -> list[dict[str, Any]]:
    """Project the conversation message list from the event log (A2 closure).

    Source of truth is the event stream: each ``LLM_REQUEST`` payload
    carries the full message list passed to the model at that turn,
    and ``CHANNEL_WRITE`` events on the ``messages`` channel carry
    incremental additions. Returns the deduplicated, time-ordered
    message list for the session. Pure read-side projection.

    Args:
        session_id: Conversation session to project.
        event_store: An ``EventStore`` implementation (in-memory or PG).

    Returns:
        List of message dicts in chronological order. Each dict has
        ``role``, ``content``, and ``created_at`` keys suitable for
        the ``MessageReadSchema`` shape.
    """
    events: list[Event] = []
    # EventStore.get_events is declared async. InMemoryEventStore
    # returns a list; PGEventStore returns an async iterator. Resolve
    # both shapes by inspecting the awaited result.
    raw = await event_store.get_events(session_id=session_id)
    events = [ev async for ev in raw] if hasattr(raw, "__aiter__") else list(raw)

    # Sort by (superstep, version) so multi-stream events stay in causal order.
    events.sort(key=lambda e: (e.superstep, getattr(e, "version", 0)))

    messages: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    for ev in events:
        etype = ev.event_type.value if hasattr(ev.event_type, "value") else str(ev.event_type)
        payload = ev.payload or {}
        if etype == EventType.LLM_REQUEST.value:
            for msg in payload.get("messages") or []:
                if not isinstance(msg, dict):
                    continue
                key = (msg.get("role", ""), json.dumps(msg.get("content"), sort_keys=True, default=str))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                messages.append(
                    {
                        "role": msg.get("role", ""),
                        "content": msg.get("content"),
                        "created_at": ev.timestamp.isoformat() if ev.timestamp else None,
                    }
                )
        elif etype == EventType.CHANNEL_WRITE.value:
            if payload.get("channel") != "messages":
                continue
            value = payload.get("value")
            if isinstance(value, dict):
                key = (value.get("role", ""), json.dumps(value.get("content"), sort_keys=True, default=str))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                messages.append(
                    {
                        "role": value.get("role", ""),
                        "content": value.get("content"),
                        "created_at": ev.timestamp.isoformat() if ev.timestamp else None,
                    }
                )

    return messages
