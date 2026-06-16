"""Tests for session lock manager and task queuing.

Covers:
- SessionLockManager acquire/release
- Queue position tracking
- Timeout handling (408)
- Concurrent messages for same session processed sequentially
- Different sessions process independently
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from hecate.services.session_lock import SessionLockManager


async def test_acquire_release() -> None:
    manager = SessionLockManager(default_timeout=5.0)

    async with manager.acquire("session-1") as info:
        assert info["queue_position"] == 0
        assert info["wait_ms"] >= 0

    assert manager.get_queue_position("session-1") == 0


async def test_queue_position_tracking() -> None:
    manager = SessionLockManager(default_timeout=5.0)

    results: list[int] = []

    async def worker(session_id: str, delay: float) -> None:
        async with manager.acquire(session_id) as info:
            results.append(info["queue_position"])
            await asyncio.sleep(delay)

    task1 = asyncio.create_task(worker("s1", 0.1))
    await asyncio.sleep(0.01)
    task2 = asyncio.create_task(worker("s1", 0.1))
    await asyncio.sleep(0.01)
    task3 = asyncio.create_task(worker("s1", 0.1))

    await asyncio.gather(task1, task2, task3)

    assert results[0] == 0
    assert results[1] >= 1
    assert results[2] >= 2


async def test_independent_sessions() -> None:
    manager = SessionLockManager(default_timeout=5.0)

    results: list[str] = []

    async def worker(session_id: str, delay: float) -> None:
        async with manager.acquire(session_id):
            results.append(f"{session_id}-start")
            await asyncio.sleep(delay)
            results.append(f"{session_id}-end")

    task1 = asyncio.create_task(worker("s1", 0.2))
    await asyncio.sleep(0.01)
    task2 = asyncio.create_task(worker("s2", 0.05))

    await asyncio.gather(task1, task2)

    assert "s1-start" in results
    assert "s1-end" in results
    assert "s2-start" in results
    assert "s2-end" in results

    s2_end = results.index("s2-end")
    s1_end = results.index("s1-end")

    assert s2_end < s1_end


async def test_timeout_raises() -> None:
    manager = SessionLockManager(default_timeout=0.1)

    async def hold_lock() -> None:
        async with manager.acquire("s1"):
            await asyncio.sleep(1.0)

    task = asyncio.create_task(hold_lock())
    await asyncio.sleep(0.01)

    with pytest.raises(asyncio.TimeoutError):
        async with manager.acquire("s1", timeout=0.1):
            pass

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_cleanup_session() -> None:
    manager = SessionLockManager(default_timeout=5.0)

    async with manager.acquire("s1"):
        pass

    manager.cleanup_session("s1")
    assert manager.get_queue_position("s1") == 0


async def test_sequential_processing_simulation() -> None:
    manager = SessionLockManager(default_timeout=5.0)

    order: list[int] = []

    async def process(msg_id: int, delay: float) -> None:
        async with manager.acquire("session-1"):
            order.append(msg_id)
            await asyncio.sleep(delay)

    tasks = [
        asyncio.create_task(process(1, 0.1)),
        asyncio.create_task(process(2, 0.05)),
        asyncio.create_task(process(3, 0.05)),
    ]

    await asyncio.gather(*tasks)

    assert order == [1, 2, 3]
