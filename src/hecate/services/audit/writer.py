"""Batch writer for draining audit events from an async queue.

The :class:`AuditBatchWriter` runs a background drain loop that collects
audit events from an ``asyncio.Queue``, evaluates them against security
policies via the :class:`PolicyEngine`, and persists them via an
:class:`AuditStore` in batches.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from hecate.services.audit.store import AuditEvent, AuditStore

if TYPE_CHECKING:
    from hecate.services.audit.policy import PolicyEngine

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

    Optionally accepts a :class:`PolicyEngine` to evaluate each event
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
        policy_engine: PolicyEngine | None = None,
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
            except Exception as e:
                logger.error("Failed to write audit event (action=%s): %s", event.action, e)

    async def _evaluate_policies(self, batch: list[AuditEvent]) -> None:
        """Evaluate security policies against each event in the batch.

        Policy violations are logged as warnings but do **not** block
        persistence.  This keeps the audit trail complete while still
        surfacing suspicious activity for alerting.
        """
        engine = self._policy_engine
        if engine is None:
            return

        from hecate.services.audit.policy import PolicyContext

        for event in batch:
            ctx = PolicyContext()
            violations = await engine.evaluate(event, ctx)
            for violation in violations:
                logger.warning(
                    "Audit policy violation: policy=%s severity=%s user=%s action=%s reason=%s",
                    violation.policy_name,
                    violation.severity.value,
                    event.user_id,
                    event.action,
                    violation.message,
                )
