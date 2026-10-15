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
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from hecate.contracts.execution.events import EventEnvelope, EventKind
from hecate.contracts.execution.references import BackendRef, RefKind
from hecate.execution.backend import EventPage

CONTRACT_VERSION = "0.1"
PAYLOAD_SCHEMA_REF = "hecate.entry.engine_stream/0"
TERMINAL_PAYLOAD_SCHEMA_REF = "hecate.entry.terminal/0"

# Stream event keys the chat protocol layer may observe. Everything else
# the engine attaches to the event stays backend-only.
_CLIENT_SAFE_KEYS = ("type", "content")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class RunEventMapper:
    """Stateful per-run mapper from engine stream events to envelopes.

    Mapped envelopes are retained in memory for consumers of executions
    that were not persisted; when an event store is wired, every envelope
    is also appended under a deterministic run-scoped store key so paging
    survives the originating request and process (step5d contract
    closure — the durable replacement for the in-memory retention).
    """

    def __init__(self, task_ref: BackendRef, run_ref: BackendRef, *, event_store: Any | None = None) -> None:
        self._task_ref = task_ref
        self._run_ref = run_ref
        self._event_store = event_store
        # Deterministic per-run store key: stable across processes for the
        # same run reference, disjoint from session-id keys.
        self._store_key = uuid.uuid5(uuid.NAMESPACE_URL, f"run:{run_ref.issuer_domain}:{run_ref.id}")
        self._sequence = 0
        self._envelopes: list[EventEnvelope] = []
        self._terminal_envelope: EventEnvelope | None = None

    @property
    def last_sequence(self) -> int:
        return self._sequence

    async def map_stream_event(self, event: dict[str, Any]) -> EventEnvelope:
        """Map one engine stream event; unrecognised shapes map unchanged.

        The original engine event is preserved verbatim under
        ``extra["backend_detail"]``; the envelope payload carries only the
        client-safe projection so downstream protocol layers render from
        platform data. With a store wired, the envelope is appended to it
        in sequence order.
        """
        sequence = self._sequence
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
        if self._event_store is not None:
            from hecate_runtime.eventstore import Event, EventType

            await self._event_store.append(
                Event(
                    session_id=self._store_key,
                    superstep=sequence,
                    event_type=EventType.CUSTOM,
                    payload={"envelope": envelope.to_dict()},
                )
            )
        self._sequence += 1
        self._envelopes.append(envelope)
        return envelope

    async def write_terminal_envelope(
        self,
        *,
        state: str,
        stop_category: str | None = None,
        error_type: str | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> EventEnvelope:
        """Append the run's one terminal envelope (state + stop category).

        Exactly one terminal envelope is expected per run; the entry
        service writes it on both success and failure after mapping any
        recoverable engine events, so the event view carries the same
        terminal facts as the Run projection. ``error_type`` is the
        exception class name only — messages and tracebacks stay in
        server logs, never in the contract payload.
        """
        if self._terminal_envelope is not None:
            return self._terminal_envelope
        if state not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("terminal state must be succeeded, failed, or cancelled")
        payload: dict[str, Any] = {"state": state}
        if stop_category is not None:
            payload["stop_category"] = stop_category
        if error_type is not None:
            payload["error"] = {"type": error_type}
        if artifacts:
            payload["artifacts"] = artifacts
        sequence = self._sequence
        envelope = EventEnvelope(
            contract_version=CONTRACT_VERSION,
            kind=EventKind.TERMINAL,
            event_id=str(uuid.uuid4()),
            task_ref=self._task_ref,
            run_ref=self._run_ref,
            source_sequence=sequence,
            occurred_at=_utc_now(),
            received_at=_utc_now(),
            payload_schema_ref=TERMINAL_PAYLOAD_SCHEMA_REF,
            payload=payload,
            extra=extra or {},
        )
        if self._event_store is not None:
            from hecate_runtime.eventstore import Event, EventType

            await self._event_store.append(
                Event(
                    session_id=self._store_key,
                    superstep=sequence,
                    event_type=EventType.CUSTOM,
                    payload={"envelope": envelope.to_dict()},
                )
            )
        self._sequence += 1
        self._envelopes.append(envelope)
        self._terminal_envelope = envelope
        return envelope

    async def read_events(self, cursor: str | None = None, limit: int = 100) -> EventPage:
        """Page the mapped stream; the cursor is the last seen sequence.

        With a store wired, persisted envelopes are the source of truth
        (the view survives the request); the in-memory retention is the
        fallback for executions that were not persisted.
        """
        envelopes: list[EventEnvelope] | None = None
        if self._event_store is not None:
            stored = await self._event_store.get_events(self._store_key)
            persisted: list[EventEnvelope] = []
            for record in stored:
                envelope_dict = (getattr(record, "payload", None) or {}).get("envelope")
                if isinstance(envelope_dict, dict):
                    persisted.append(EventEnvelope.from_dict(envelope_dict))
            if persisted:
                envelopes = persisted
        if envelopes is None:
            envelopes = self._envelopes
        after = int(cursor) if cursor is not None else -1
        pending = [envelope for envelope in envelopes if envelope.source_sequence > after]
        window = pending[:limit]
        has_more = len(pending) > len(window)
        return EventPage(
            events=tuple(window),
            next_cursor=str(window[-1].source_sequence) if window else cursor,
            has_more=has_more,
        )


def extract_declared_artifacts(
    declared: Any,
    *,
    issuer_domain: str,
    verified_artifact_ids: Collection[str] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a result's declared artifact references into (accepted, rejected).

    A reference is accepted only when it is a well-formed ARTIFACT reference
    (``kind == "artifact"``, non-empty ``issuer_domain``/``id`` strings) whose
    ``issuer_domain`` matches the platform issuer and its id has been
    resolved in the verified workspace by the entry service. Anything else — wrong kind, foreign issuer, or a
    malformed shape — is returned in ``rejected`` with a reason so the
    contract never carries an unverifiable reference. Rejection evidence is
    backend-side (envelope ``extra``); it never flips the execution terminal
    state.
    """
    if declared is None:
        return [], []
    if not isinstance(declared, list):
        return [], [{"ref": declared, "reason": "declared artifacts must be a list"}]
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in declared:
        if not isinstance(item, dict):
            rejected.append({"ref": item, "reason": "artifact reference must be an object"})
            continue
        kind = item.get("kind")
        item_issuer = item.get("issuer_domain")
        item_id = item.get("id")
        if kind != RefKind.ARTIFACT.value:
            rejected.append({"ref": item, "reason": f"unsupported reference kind: {kind!r}"})
            continue
        if not isinstance(item_issuer, str) or not item_issuer or not isinstance(item_id, str) or not item_id:
            rejected.append({"ref": item, "reason": "artifact reference must carry issuer_domain and id"})
            continue
        if item_issuer != issuer_domain:
            rejected.append({"ref": item, "reason": f"foreign issuer domain: {item_issuer!r}"})
            continue
        if item_id not in verified_artifact_ids:
            rejected.append({"ref": item, "reason": "artifact ownership is not verified in workspace"})
            continue
        accepted.append({"kind": kind, "issuer_domain": item_issuer, "id": item_id})
    return accepted, rejected


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
