"""Batch writer for draining audit events from an async queue.

The :class:`AuditBatchWriter` runs a background drain loop that collects
audit events from an ``asyncio.Queue``, evaluates them against security
policies via the :class:`FindingEngine`, and persists them via an
:class:`AuditStore` in batches.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from hecate.services.audit.store import AuditEvent, AuditStore

if TYPE_CHECKING:
    from hecate.services.audit.policy import FindingEngine

logger = logging.getLogger(__name__)


@dataclass
class WriterConfig:
    """Configuration for the audit batch writer.

    Attributes:
        batch_size: Maximum events to collect before flushing.
        flush_interval_seconds: Maximum time to wait before flushing a partial batch.
    """

    batch_size: int = 50
    flush_interval_seconds: float = 2.0


class AuditBatchWriter:
    """Background writer that drains audit events from an async queue.

    Optionally accepts a :class:`FindingEngine` to evaluate each event
    for security policy violations before persistence.

    Typical usage::

        store = DatabaseAuditStore()
        queue: asyncio.Queue[AuditEvent] = asyncio.Queue()
        writer = AuditBatchWriter(store, queue, policy_engine=engine)

        # In lifespan startup:
        await writer.start()

        # Middleware puts events:
        await queue.put(event)

        # In lifespan shutdown:
        await writer.stop()
    """

    def __init__(
        self,
        store: AuditStore,
        queue: asyncio.Queue[AuditEvent],
        config: WriterConfig | None = None,
        policy_engine: FindingEngine | None = None,
    ) -> None:
        self._store = store
        self._queue = queue
        self._config = config or WriterConfig()
        self._policy_engine = policy_engine
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background drain loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._drain_loop())
        logger.info("AuditBatchWriter started (batch_size=%d)", self._config.batch_size)

    async def stop(self) -> None:
        """Signal the drain loop to stop and flush remaining events."""
        self._running = False
        if self._task is not None:
            await self._task
            self._task = None
        # Flush any remaining events
        await self._flush_remaining()
        logger.info("AuditBatchWriter stopped")

    def get_queue(self) -> asyncio.Queue[AuditEvent]:
        """Return the queue for producers to put events into."""
        return self._queue

    async def _drain_loop(self) -> None:
        """Main drain loop: collect events and batch-write them."""
        batch: list[AuditEvent] = []

        while self._running or not self._queue.empty():
            try:
                event = await asyncio.wait_for(
                    self._queue.get(),
                    timeout=self._config.flush_interval_seconds,
                )
                batch.append(event)
            except TimeoutError:
                pass

            if len(batch) >= self._config.batch_size or (batch and not self._running):
                await self._write_batch(batch)
                batch = []

        if batch:
            await self._write_batch(batch)

    async def _flush_remaining(self) -> None:
        """Flush any events left in the queue."""
        batch: list[AuditEvent] = []
        while not self._queue.empty():
            try:
                event = self._queue.get_nowait()
                batch.append(event)
            except asyncio.QueueEmpty:
                break
        if batch:
            await self._write_batch(batch)

    async def _write_batch(self, batch: list[AuditEvent]) -> None:
        """Persist a batch of events, evaluating security policies first."""
        if self._policy_engine is not None:
            await self._evaluate_policies(batch)
        for event in batch:
            try:
                await self._store.write(event)
                self._emit_audit_to_siem(event)
            except Exception as e:
                logger.error("Failed to write audit event (action=%s): %s", event.action, e)

    def _emit_audit_to_siem(self, event: AuditEvent) -> None:
        """Emit an audit event to the SIEM export pipeline."""
        with contextlib.suppress(Exception):
            from hecate.services.security.siem.collector import emit_to_siem
            from hecate.services.security.siem.event import from_audit_log

            siem_event = from_audit_log(
                action=event.action,
                success=event.success,
                response_status=event.response_status,
                user_id=str(event.user_id) if event.user_id else None,
                org_id=str(event.org_id) if event.org_id else None,
                workspace_id=str(event.workspace_id) if event.workspace_id else None,
                request_method=event.request_method,
                request_path=event.request_path,
                ip_address=event.ip_address,
                timestamp=event.timestamp,
            )
            emit_to_siem(siem_event)

    async def _evaluate_policies(self, batch: list[AuditEvent]) -> None:
        """Evaluate security policies against each event in the batch.

        Findings are persisted to the ``security_findings`` table via
        ``SecurityFindingModel``. Persistence failures are logged but do
        not block audit event writing.
        """
        engine = self._policy_engine
        if engine is None:
            return

        from hecate.core.database import async_session_factory
        from hecate.models.security_finding import SecurityFindingModel
        from hecate.services.audit.policy import DetectionContext

        for event in batch:
            ctx = DetectionContext()
            violations = await engine.evaluate(event, ctx)
            for finding in violations:
                logger.debug(
                    "Audit finding: rule=%s severity=%s user=%s action=%s",
                    finding.rule_name,
                    finding.severity.value,
                    event.user_id,
                    event.action,
                )
                try:
                    async with async_session_factory() as session:
                        row = SecurityFindingModel(
                            org_id=event.org_id,
                            workspace_id=event.workspace_id,
                            user_id=event.user_id,
                            rule_name=finding.rule_name,
                            severity=finding.severity.value,
                            message=finding.message,
                            source_event={
                                "action": event.action,
                                "resource_type": event.resource_type,
                                "resource_id": str(event.resource_id) if event.resource_id else None,
                            },
                            metadata_=finding.metadata,
                        )
                        session.add(row)
                        await session.commit()
                except Exception:
                    logger.exception(
                        "Failed to persist security finding (rule=%s)",
                        finding.rule_name,
                    )

                # Emit finding to SIEM export pipeline
                with contextlib.suppress(Exception):
                    from hecate.services.security.siem.collector import emit_to_siem
                    from hecate.services.security.siem.event import from_security_finding

                    siem_event = from_security_finding(
                        rule_name=finding.rule_name,
                        severity=finding.severity.value,
                        message=finding.message,
                        org_id=str(event.org_id) if event.org_id else None,
                        workspace_id=str(event.workspace_id) if event.workspace_id else None,
                        user_id=str(event.user_id) if event.user_id else None,
                        finding_metadata=finding.metadata,
                    )
                    emit_to_siem(siem_event)
