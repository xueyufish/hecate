"""Engine stream events mapped to the platform contract (step5d).

The entry adapter only sees platform events: this module converts one
run's engine stream (``{"type": "message", ...}`` / ``{"type": "values",
...}`` shapes yielded by the Pregel chat path) into ``EventEnvelope``
records with task/run references and a monotonic per-run sequence, and
validates TOOL_CALL/TOOL_RESULT pairing from the engine event log.

Client-safe field selection happens here, not in the protocol layer:
only fields a chat client may observe enter ``payload``; every other
engine detail travels in ``extra["backend_detail"]`` so protocol
adapters cannot leak engine internals into OpenAI-style chunks.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from hecate.contracts.execution.events import EventEnvelope, EventKind
from hecate.contracts.execution.references import BackendRef
from hecate.execution.backend import EventPage

CONTRACT_VERSION = "0.1"
PAYLOAD_SCHEMA_REF = "hecate.entry.engine_stream/0"

# Stream event keys the chat protocol layer may observe. Everything else
# the engine attaches to the event stays backend-only.
_CLIENT_SAFE_KEYS = ("type", "content")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class RunEventMapper:
    """Stateful per-run mapper from engine stream events to envelopes.

    Mapped envelopes are retained so a consumer can page the run's
    platform event stream by the opaque cursor returned with each page
    (step6 replaces the in-memory retention with the durable store).
    """

    def __init__(self, task_ref: BackendRef, run_ref: BackendRef) -> None:
        self._task_ref = task_ref
        self._run_ref = run_ref
        self._sequence = 0
        self._envelopes: list[EventEnvelope] = []

    @property
    def last_sequence(self) -> int:
        return self._sequence

    def map_stream_event(self, event: dict[str, Any]) -> EventEnvelope:
        """Map one engine stream event; unrecognised shapes map unchanged.

        The original engine event is preserved verbatim under
        ``extra["backend_detail"]``; the envelope payload carries only the
        client-safe projection so downstream protocol layers render from
        platform data.
        """
        sequence = self._sequence
        self._sequence += 1
        payload: dict[str, Any]
        if event.get("type") == "values":
            # The values stream nests client-observable state under
            # ``state``; only the suggestion list is client-safe there.
            state = event.get("state") or {}
            payload = {"type": "values"}
            if state.get("suggested_questions") is not None:
                payload["suggested_questions"] = state["suggested_questions"]
            payload = {key: value for key, value in payload.items() if value is not None}
        else:
            payload = {key: event.get(key) for key in _CLIENT_SAFE_KEYS if event.get(key) is not None}
        envelope = EventEnvelope(
            contract_version=CONTRACT_VERSION,
            kind=EventKind.EVENT,
            event_id=str(uuid.uuid4()),
            task_ref=self._task_ref,
            run_ref=self._run_ref,
            source_sequence=sequence,
            occurred_at=_utc_now(),
            received_at=_utc_now(),
            payload_schema_ref=PAYLOAD_SCHEMA_REF,
            payload=payload,
            extra={"backend_detail": dict(event)},
        )
        self._envelopes.append(envelope)
        return envelope

    def read_events(self, cursor: str | None = None, limit: int = 100) -> EventPage:
        """Page the mapped stream; the cursor is the last seen sequence.

        Returns consecutive envelopes with ``has_more``/``next_cursor`` so
        consumers resume where they stopped instead of re-reading.
        """
        start = 0
        if cursor is not None:
            start = int(cursor) + 1
        window = self._envelopes[start : start + limit]
        next_index = start + len(window)
        has_more = next_index < len(self._envelopes)
        return EventPage(
            events=tuple(window),
            next_cursor=str(window[-1].source_sequence) if window else cursor,
            has_more=has_more,
        )


@dataclass(frozen=True)
class ToolPairingReport:
    """Outcome of TOOL_CALL/TOOL_RESULT pairing over one run's event log."""

    paired: tuple[dict[str, Any], ...]
    unpaired_calls: tuple[dict[str, Any], ...]
    unpaired_results: tuple[dict[str, Any], ...]

    @property
    def ok(self) -> bool:
        return not self.unpaired_calls and not self.unpaired_results


def validate_tool_pairing(events: Iterable[Any]) -> ToolPairingReport:
    """Pair engine TOOL_CALL/TOOL_RESULT events by ``execution_id``.

    ``events`` are engine event-log records exposing ``event_type`` and
    ``payload`` (the runtime ``Event`` dataclass shape). Every claim must
    have a receipt and every receipt a claim; leftovers are reported, not
    silently dropped.
    """
    calls: dict[str, dict[str, Any]] = {}
    results: dict[str, dict[str, Any]] = {}
    for event in events:
        payload = getattr(event, "payload", None)
        if not isinstance(payload, dict):
            continue
        execution_id = payload.get("execution_id")
        if not execution_id:
            continue
        event_type = str(getattr(event, "event_type", ""))
        if event_type == "TOOL_CALL":
            calls[execution_id] = dict(payload)
        elif event_type == "TOOL_RESULT":
            results[execution_id] = dict(payload)
    paired = tuple(
        {"execution_id": eid, "tool_name": calls[eid].get("tool_name"), "status": results[eid].get("status")}
        for eid in calls
        if eid in results
    )
    unpaired_calls = tuple(
        {"execution_id": eid, "tool_name": c.get("tool_name")} for eid, c in calls.items() if eid not in results
    )
    unpaired_results = tuple(
        {"execution_id": eid, "status": r.get("status")} for eid, r in results.items() if eid not in calls
    )
    return ToolPairingReport(paired=paired, unpaired_calls=unpaired_calls, unpaired_results=unpaired_results)
