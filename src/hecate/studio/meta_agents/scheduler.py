"""Scheduler for running meta-agents on a configurable cron schedule.

Provides a lightweight async scheduler that invokes meta-agents at
configurable intervals without external cron dependencies.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ScheduleConfig:
    """Configuration for a meta-agent schedule entry."""

    name: str
    interval_seconds: int = 3600
    enabled: bool = True


@dataclass
class ScheduleResult:
    """Result of a single scheduled run."""

    agent_name: str
    started_at: datetime
    finished_at: datetime | None = None
    success: bool = False
    error: str | None = None


type AgentCoroutine = Callable[..., Coroutine[Any, Any, Any]]


class MetaAgentScheduler:
    """Runs meta-agents on configurable intervals."""

    def __init__(self) -> None:
        self._schedules: dict[str, ScheduleConfig] = {}
        self._agents: dict[str, AgentCoroutine] = {}
        self._results: list[ScheduleResult] = []
        self._running = False
        self._tasks: list[asyncio.Task[None]] = []

    def register(
        self,
        name: str,
        agent_fn: AgentCoroutine,
        interval_seconds: int = 3600,
    ) -> None:
        """Register a meta-agent with a schedule."""
        self._schedules[name] = ScheduleConfig(name=name, interval_seconds=interval_seconds)
        self._agents[name] = agent_fn
        logger.info("Registered meta-agent '%s' (interval=%ds)", name, interval_seconds)

    async def _run_agent(self, name: str) -> ScheduleResult:
        """Execute a single meta-agent and record the result."""
        result = ScheduleResult(
            agent_name=name,
            started_at=datetime.now(UTC),
        )
        try:
            await self._agents[name]()
            result.success = True
        except Exception as exc:
            result.error = str(exc)
            logger.exception("Meta-agent '%s' failed", name)
        result.finished_at = datetime.now(UTC)
        self._results.append(result)
        return result

    async def _schedule_loop(self, name: str) -> None:
        """Run a single agent on its configured interval."""
        config = self._schedules[name]
        while self._running and config.enabled:
            await self._run_agent(name)
            await asyncio.sleep(config.interval_seconds)

    async def start(self) -> None:
        """Start all registered agent schedules."""
        self._running = True
        for name in self._schedules:
            task = asyncio.create_task(self._schedule_loop(name))
            self._tasks.append(task)
        logger.info("Scheduler started with %d agents", len(self._schedules))

    async def stop(self) -> None:
        """Stop all running schedules."""
        self._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("Scheduler stopped")

    def get_results(self) -> list[ScheduleResult]:
        """Return all recorded schedule results."""
        return list(self._results)
