"""Unit tests for MetaAgentScheduler."""

from __future__ import annotations

import asyncio

import pytest

from hecate.services.meta_agents.scheduler import (
    MetaAgentScheduler,
)


@pytest.fixture
def scheduler() -> MetaAgentScheduler:
    return MetaAgentScheduler()


async def test_register_agent(scheduler: MetaAgentScheduler) -> None:
    called = False

    async def dummy_agent() -> None:
        nonlocal called
        called = True

    scheduler.register("test", dummy_agent, interval_seconds=1)
    assert "test" in scheduler._schedules
    assert scheduler._schedules["test"].interval_seconds == 1


async def test_start_and_stop(scheduler: MetaAgentScheduler) -> None:
    run_count = 0

    async def counting_agent() -> None:
        nonlocal run_count
        run_count += 1

    scheduler.register("counter", counting_agent, interval_seconds=1)
    await scheduler.start()
    await asyncio.sleep(0.2)
    await scheduler.stop()
    assert run_count >= 1


async def test_run_agent_records_result(scheduler: MetaAgentScheduler) -> None:
    async def ok_agent() -> None:
        pass

    scheduler.register("ok", ok_agent)
    result = await scheduler._run_agent("ok")
    assert result.success is True
    assert result.finished_at is not None
    assert result.error is None


async def test_run_agent_records_error(scheduler: MetaAgentScheduler) -> None:
    async def bad_agent() -> None:
        raise ValueError("boom")

    scheduler.register("bad", bad_agent)
    result = await scheduler._run_agent("bad")
    assert result.success is False
    assert "boom" in (result.error or "")


async def test_get_results(scheduler: MetaAgentScheduler) -> None:
    async def agent() -> None:
        pass

    scheduler.register("r", agent)
    await scheduler._run_agent("r")
    results = scheduler.get_results()
    assert len(results) == 1
    assert results[0].agent_name == "r"
