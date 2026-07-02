"""Tests for AuditBatchWriter — batch drain, policy evaluation, error handling."""

from __future__ import annotations

import asyncio
import uuid

from hecate.services.audit.policy import PolicyEngine, UnusualIPDetectionPolicy
from hecate.services.audit.store import AuditEvent, AuditStore
from hecate.services.audit.writer import AuditBatchWriter, WriterConfig


class InMemoryAuditStore(AuditStore):
    """In-memory audit store for testing."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def write(self, event: AuditEvent) -> None:
        self.events.append(event)

    async def query(self, filters: object) -> tuple[list[object], int]:
        return [], 0

    async def export(self, fmt: str, filters: object) -> str:
        return "[]"

    async def archive(self, before_date: object) -> int:
        return 0


def _make_event(action: str = "api.test.create") -> AuditEvent:
    return AuditEvent(org_id=uuid.UUID(int=1), user_id=uuid.UUID(int=1), action=action)


class TestAuditBatchWriter:
    async def test_write_single_event(self) -> None:
        store = InMemoryAuditStore()
        queue: asyncio.Queue[AuditEvent] = asyncio.Queue()
        writer = AuditBatchWriter(store, queue, config=WriterConfig(batch_size=1))

        await queue.put(_make_event())
        await writer.start()
        await asyncio.sleep(0.1)
        await writer.stop()

        assert len(store.events) == 1

    async def test_batch_multiple_events(self) -> None:
        store = InMemoryAuditStore()
        queue: asyncio.Queue[AuditEvent] = asyncio.Queue()
        writer = AuditBatchWriter(store, queue, config=WriterConfig(batch_size=5))

        for _ in range(3):
            await queue.put(_make_event())

        await writer.start()
        await asyncio.sleep(0.5)
        await writer.stop()

        assert len(store.events) == 3

    async def test_write_error_does_not_crash(self) -> None:
        class FailingStore(InMemoryAuditStore):
            async def write(self, event: AuditEvent) -> None:
                raise RuntimeError("DB error")

        store = FailingStore()
        queue: asyncio.Queue[AuditEvent] = asyncio.Queue()
        writer = AuditBatchWriter(store, queue, config=WriterConfig(batch_size=1))

        await queue.put(_make_event())
        await writer.start()
        await asyncio.sleep(0.1)
        await writer.stop()

    async def test_policy_engine_integration(self) -> None:
        store = InMemoryAuditStore()
        queue: asyncio.Queue[AuditEvent] = asyncio.Queue()
        engine = PolicyEngine()
        engine.register(UnusualIPDetectionPolicy())
        writer = AuditBatchWriter(store, queue, config=WriterConfig(batch_size=1), policy_engine=engine)

        event = AuditEvent(
            org_id=uuid.UUID(int=1),
            user_id=uuid.UUID(int=1),
            action="api.test.create",
            ip_address="10.0.0.1",
        )
        await queue.put(event)
        await writer.start()
        await asyncio.sleep(0.1)
        await writer.stop()

        assert len(store.events) == 1

    async def test_flush_on_stop(self) -> None:
        store = InMemoryAuditStore()
        queue: asyncio.Queue[AuditEvent] = asyncio.Queue()
        writer = AuditBatchWriter(store, queue, config=WriterConfig(batch_size=100))

        for _ in range(5):
            await queue.put(_make_event())

        await writer.start()
        await asyncio.sleep(0.1)
        await writer.stop()

        assert len(store.events) == 5
