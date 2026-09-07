"""Evidence tracking for tool execution results (Context Engineering).

Provides the ``EvidenceTracker`` — an in-session, in-memory store of
structured tool execution captures with provenance metadata, importance
scoring, and re-reference boosting. One tracker is created per run and
injected into the execution context (like ``context_engine`` /
``context_offloader``); ``ToolWorker`` captures one record per tool
execution, and the run's snapshot can be persisted afterwards (see
``WorkflowExecutionService``) so records survive the process.

Importance heuristic (4.8):

- base score ``0.5``
- ``+0.2`` when the same tool is called again with equivalent arguments
  (re-reference boosting — the agent found the earlier result useful
  enough to repeat the call)
- ``+0.1`` for error results (diagnostic value)
- ``+0.1`` when the caller passes ``reused=True`` explicitly

The tracker never raises: capture failures degrade to a warning, mirroring
the best-effort contract of the security finding writer.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

_BASE_IMPORTANCE = 0.5
_REREFERENCE_BOOST = 0.2
_ERROR_BOOST = 0.1
_EXPLICIT_REUSE_BOOST = 0.1
_MAX_RAW_CONTENT_CHARS = 8_000


def _signature(tool_name: str, arguments: dict[str, Any] | None) -> str:
    """Build a stable dedup/re-reference signature for a tool call."""
    try:
        canonical = json.dumps(arguments or {}, sort_keys=True, default=str)
    except (TypeError, ValueError):
        canonical = str(arguments)
    return f"{tool_name}::{canonical}"


@dataclass
class EvidenceRecord:
    """Single captured tool execution result with provenance."""

    evidence_id: str
    session_id: str
    tool_name: str
    tool_arguments: dict[str, Any]
    raw_content: str
    normalized_content: dict[str, Any]
    is_error: bool
    importance: float
    source_type: str
    provenance: dict[str, Any]
    created_at: str
    references: int = 1
    signature: str = field(default="", repr=False)


class EvidenceTracker:
    """Per-run evidence store for tool execution results.

    Captured records are kept in memory for the duration of one run and
    exposed via ``snapshot()`` for persistence. ``match_existing`` lets the
    caller apply re-reference boosting: when a tool call repeats an earlier
    equivalent call, the earlier record's importance is raised instead of
    silently duplicating it.
    """

    def __init__(self, session_id: str | uuid.UUID = "") -> None:
        self._session_id = str(session_id)
        self._records: list[EvidenceRecord] = []
        self._by_signature: dict[str, list[EvidenceRecord]] = {}

    @property
    def session_id(self) -> str:
        """Session the captured evidence belongs to."""
        return self._session_id

    @property
    def records(self) -> list[EvidenceRecord]:
        """Captured records in insertion order."""
        return list(self._records)

    def __len__(self) -> int:
        return len(self._records)

    def match_existing(self, tool_name: str, arguments: dict[str, Any] | None) -> EvidenceRecord | None:
        """Return the most recent equivalent record, boosting its importance.

        Re-reference boosting: repeating a tool call with equivalent
        arguments signals the earlier result is being relied upon — bump
        its importance so context prioritization keeps it longer.
        """
        sig = _signature(tool_name, arguments)
        matches = self._by_signature.get(sig)
        if not matches:
            return None
        record = matches[-1]
        record.references += 1
        record.importance = min(1.0, record.importance + _REREFERENCE_BOOST)
        return record

    def capture(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        raw_content: Any = None,
        is_error: bool = False,
        node_id: str = "",
        superstep: int = 0,
        source_type: str = "tool",
        reused: bool = False,
    ) -> EvidenceRecord:
        """Capture one tool execution result. Best-effort — never raises.

        Args:
            tool_name: Name of the executed tool.
            arguments: Arguments the tool was called with.
            raw_content: Raw tool return value (stringified for storage).
            is_error: Whether the execution failed.
            node_id: Graph node that dispatched the call (provenance).
            superstep: BSP superstep of the dispatch (provenance).
            source_type: Evidence source ("tool", "knowledge", "user").
            reused: Caller-asserted re-reference of an earlier result.

        Returns:
            The stored EvidenceRecord (or a detached dummy when storage
            fails, so callers can proceed unconditionally).
        """
        try:
            importance = _BASE_IMPORTANCE
            if is_error:
                importance += _ERROR_BOOST
            if reused:
                importance += _EXPLICIT_REUSE_BOOST

            if isinstance(raw_content, str):
                raw_text = raw_content
                normalized: dict[str, Any] = {"text": raw_content}
            elif isinstance(raw_content, dict):
                raw_text = json.dumps(raw_content, default=str)
                normalized = raw_content
            else:
                raw_text = "" if raw_content is None else str(raw_content)
                normalized = {"value": raw_content}
            if len(raw_text) > _MAX_RAW_CONTENT_CHARS:
                raw_text = raw_text[:_MAX_RAW_CONTENT_CHARS]

            record = EvidenceRecord(
                evidence_id=uuid.uuid4().hex,
                session_id=self._session_id,
                tool_name=tool_name,
                tool_arguments=dict(arguments or {}),
                raw_content=raw_text,
                normalized_content=normalized,
                is_error=is_error,
                importance=min(1.0, importance),
                source_type=source_type,
                provenance={"node_id": node_id, "superstep": superstep},
                created_at=datetime.now(UTC).isoformat(),
                signature=_signature(tool_name, arguments),
            )
            self._records.append(record)
            self._by_signature.setdefault(record.signature, []).append(record)
            return record
        except Exception:
            logger.warning("EvidenceTracker.capture failed for tool '%s'", tool_name, exc_info=True)
            return EvidenceRecord(
                evidence_id="",
                session_id=self._session_id,
                tool_name=tool_name,
                tool_arguments=dict(arguments or {}),
                raw_content="",
                normalized_content={},
                is_error=is_error,
                importance=0.0,
                source_type=source_type,
                provenance={},
                created_at="",
            )

    def snapshot(self, min_importance: float | None = None) -> list[EvidenceRecord]:
        """Return captured records, optionally filtered by importance."""
        if min_importance is None:
            return list(self._records)
        return [r for r in self._records if r.importance >= min_importance]
