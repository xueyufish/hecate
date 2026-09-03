"""Output-side security finding writer.

Single chokepoint for persisting ``SecurityFindingModel`` rows from the output
post-LLM pipeline (DLP, injection detection, prompt leakage). The historical
``OutputSecurityHook.security_finding_writer`` callable contract is preserved
via the ``FindingWriterAdapter`` for backward compatibility with tests.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.security_finding import SecurityFindingModel

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FindingTuple:
    """Single output-side detection finding for batch writes."""

    entity_type: str
    value: str
    start: int
    end: int
    score: float
    recognizer: str
    action: str
    severity: str = "high"
    rule_name: str | None = None
    context: dict[str, Any] | None = None


class SecurityFindingWriter:
    """Persist ``SecurityFindingModel`` rows for output-side detections.

    Constructed once per turn (``assemble_guardrails`` lifetime) with the
    workspace / session / event_store context. All three new capabilities
    (9.1a injection detection, 9.2 prompt leakage, the existing DLP output
    scan) funnel findings through this writer.

    Failures during ``write`` are caught and logged — the post-LLM pipeline
    SHALL NOT fail because finding persistence fails (best-effort audit).
    """

    def __init__(
        self,
        *,
        db: AsyncSession | None,
        org_id: uuid.UUID | None,
        workspace_id: uuid.UUID | None,
        session_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        event_store: Any = None,
        emit_event: bool = True,
    ) -> None:
        self._db = db
        self._org_id = org_id
        self._workspace_id = workspace_id
        self._session_id = session_id
        self._user_id = user_id
        self._event_store = event_store
        self._emit_event = emit_event
        self._warned_no_db = False

    @property
    def is_active(self) -> bool:
        """True when the writer has the minimum context to persist rows."""
        return self._db is not None and self._org_id is not None and self._workspace_id is not None

    async def write(
        self,
        *,
        entity_type: str,
        value: str,
        start: int,
        end: int,
        score: float,
        recognizer: str,
        action: str,
        severity: str = "high",
        rule_name: str | None = None,
        source: str = "output",
        context: dict[str, Any] | None = None,
    ) -> SecurityFindingModel | None:
        """Persist a single finding row. Returns None on skip / failure."""
        if not self.is_active:
            if not self._warned_no_db:
                logger.warning("SecurityFindingWriter skipping writes: missing db/org/workspace context")
                self._warned_no_db = True
            return None

        effective_rule_name = rule_name or f"output.{source}.{recognizer}"
        truncated_value = (value or "")[:256]
        metadata = {
            "source": source,
            "recognizer": recognizer,
            "context": context or {},
            "span": [start, end],
            "action": action,
        }

        try:
            row = SecurityFindingModel(
                org_id=self._org_id,
                workspace_id=self._workspace_id,
                user_id=self._user_id,
                rule_name=effective_rule_name,
                severity=severity,
                message=f"{entity_type}: {truncated_value}",
                source_event=None,
                metadata_=metadata,
            )
            self._db.add(row)
            await self._db.flush()
        except Exception as exc:
            logger.warning(
                "SecurityFindingWriter.write failed (entity=%s, recognizer=%s): %s",
                entity_type,
                recognizer,
                exc.__class__.__name__,
            )
            return None

        if self._emit_event and self._event_store is not None and self._session_id is not None:
            await self._emit_event_async(event_type="security_finding", finding=row, payload=metadata)

        return row

    async def write_many(self, findings: Iterable[FindingTuple], *, source: str = "output") -> int:
        """Persist multiple findings in one transaction; returns count written."""
        written = 0
        for f in findings:
            row = await self.write(
                entity_type=f.entity_type,
                value=f.value,
                start=f.start,
                end=f.end,
                score=f.score,
                recognizer=f.recognizer,
                action=f.action,
                severity=f.severity,
                rule_name=f.rule_name,
                source=source,
                context=f.context,
            )
            if row is not None:
                written += 1
        return written

    async def _emit_event_async(
        self, *, event_type: str, finding: SecurityFindingModel, payload: dict[str, Any]
    ) -> None:
        try:
            from hecate.runtime.eventstore import Event

            event = Event(
                session_id=self._session_id,
                superstep=0,
                event_type=event_type,
                payload={
                    "finding_id": str(finding.id),
                    "rule_name": finding.rule_name,
                    "severity": finding.severity,
                    **payload,
                },
            )
            await self._event_store.append(event)
        except Exception as exc:
            logger.warning(
                "SecurityFindingWriter event emission failed (rule=%s): %s",
                finding.rule_name,
                exc.__class__.__name__,
            )


class FindingWriterAdapter:
    """Adapt a legacy callable ``security_finding_writer`` to the new writer shape.

    Preserves backward compatibility for tests / third-party callers that
    construct ``OutputSecurityHook`` directly with a callable writer.
    """

    def __init__(self, callable_writer: Any) -> None:
        self._callable = callable_writer

    async def write(self, **kwargs: Any) -> Any:
        kwargs.setdefault("rule_name", None)
        kwargs.setdefault("source", "output")
        kwargs.setdefault("context", None)
        return self._callable(**kwargs)

    @property
    def is_active(self) -> bool:
        return self._callable is not None
