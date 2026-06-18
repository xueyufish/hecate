"""Retry strategy and executor for graph node execution.

Defines the ``RetryStrategy`` abstract base class (12th engine ABC) and
``RetryExecutor`` component that wraps worker dispatch with retry logic.

Design decisions (see openspec/changes/framework-auto-retry/design.md):
- RetryStrategy ABC lives in engine/ (zero external deps)
- DefaultRetryStrategy implementation lives in services/ (uses ErrorClassifier)
- Stream-safe retry: retry only before first token yielded
- EventStore observability via CUSTOM events
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any

from hecate.engine.eventstore import Event, EventType
from hecate.engine.types import WorkerResult

logger = logging.getLogger(__name__)


class RetryStrategy(ABC):
    """Abstract interface for retry decisions.

    Implementations decide whether an error warrants a retry and how long
    to wait before the next attempt. Follows the same pluggable pattern as
    SchedulerStrategy, EvictionPolicy, and other engine ABCs.

    The strategy is stateless — it does not track attempt history. The
    RetryExecutor passes the current attempt number (0-based) to each call.
    """

    @abstractmethod
    def should_retry(self, error: Exception, attempt: int) -> bool:
        """Decide if the given error warrants a retry at the given attempt.

        Args:
            error: The exception that caused the failure.
            attempt: The current attempt number (0-based).

        Returns:
            True if the executor should retry, False to propagate the error.
        """
        ...

    @abstractmethod
    def get_backoff(self, attempt: int) -> float:
        """Calculate the backoff delay before the next attempt.

        Args:
            attempt: The current attempt number (0-based).

        Returns:
            Delay in seconds before the next retry attempt.
        """
        ...

    def with_config(self, **overrides: Any) -> RetryStrategy:
        """Create a copy with configuration overrides.

        Default implementation returns ``self`` (no-op). Subclasses with
        configurable parameters (e.g., DefaultRetryStrategy) override this
        to produce a new instance with merged settings.

        Args:
            **overrides: Configuration key-value pairs to override.

        Returns:
            A RetryStrategy instance, by default the same instance.
        """
        return self


class NoRetryStrategy(RetryStrategy):
    """Default retry strategy — never retries.

    Preserves the pre-1.3.5h behavior where worker errors propagate
    immediately. Used as the default when PregelRuntime is constructed
    without an explicit retry_strategy parameter.
    """

    def should_retry(self, error: Exception, attempt: int) -> bool:
        """Always returns False — no retry."""
        return False

    def get_backoff(self, attempt: int) -> float:
        """Always returns 0.0 — no delay."""
        return 0.0


class RetryExecutor:
    """Wraps worker execution with retry logic.

    Handles two execution paths:
    1. **Non-streaming**: ``execute()`` calls the function, checks
       ``WorkerResult.error``, retries per strategy.
    2. **Streaming**: ``execute_stream()`` iterates the async generator,
       retries only before the first token is yielded (stream-safe).

    EventStore integration: when an ``execution_context`` dict containing
    an ``event_store`` is passed through kwargs, each retry emits a CUSTOM
    event with node_id, attempt, error details, and backoff delay.
    """

    def __init__(self, strategy: RetryStrategy | None = None) -> None:
        """Initialize the executor with a retry strategy.

        Args:
            strategy: The retry strategy to use. Defaults to NoRetryStrategy.
        """
        self._strategy = strategy or NoRetryStrategy()

    @property
    def strategy(self) -> RetryStrategy:
        """Return the active retry strategy."""
        return self._strategy

    async def execute(self, func: Any, *args: Any, **kwargs: Any) -> WorkerResult:
        """Execute a non-streaming function with retry logic.

        The function must return a ``WorkerResult``. If the result has an
        error and the strategy permits retry, the executor sleeps for the
        strategy's backoff duration and retries.

        Args:
            func: Async callable that returns WorkerResult.
            *args: Positional arguments forwarded to func.
            **kwargs: Keyword arguments forwarded to func. If
                ``execution_context`` is present, it is used for EventStore
                observability.

        Returns:
            The final WorkerResult (success or last failure).
        """
        attempt = 0
        ctx = kwargs.get("execution_context")
        while True:
            result = await func(*args, **kwargs)
            if result.error is None:
                return result
            if not self._strategy.should_retry(result.error, attempt):
                return result
            delay = self._strategy.get_backoff(attempt)
            logger.warning(
                "Retrying node '%s' after %s (attempt %d, delay %.1fs)",
                result.node_id,
                type(result.error).__name__,
                attempt + 1,
                delay,
            )
            await self._emit_retry_event(ctx, result.error, attempt, delay, result.node_id)
            await asyncio.sleep(delay)
            attempt += 1

    async def execute_stream(
        self,
        func: Any,
        *args: Any,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any] | WorkerResult, None]:
        """Execute a streaming function with stream-safe retry logic.

        Iterates the async generator produced by ``func``. Token dicts are
        forwarded to the caller. The final ``WorkerResult`` is yielded last.

        **Stream-safe retry**: retry is only attempted if no tokens have been
        yielded to the caller. Once the first token is forwarded, any error
        propagates immediately to prevent token duplication.

        Args:
            func: Async callable that returns an async generator yielding
                ``dict[str, Any]`` tokens and a final ``WorkerResult``.
            *args: Positional arguments forwarded to func.
            **kwargs: Keyword arguments forwarded to func. If
                ``execution_context`` is present, it is used for EventStore
                observability.

        Yields:
            Token dicts and the final WorkerResult.
        """
        attempt = 0
        ctx = kwargs.get("execution_context")
        node_id = args[0] if args else ""
        while True:
            first_token_yielded = False
            should_retry = False
            last_error: Exception | None = None

            try:
                async for item in func(*args, **kwargs):
                    if isinstance(item, WorkerResult):
                        if (
                            item.error is not None
                            and not first_token_yielded
                            and self._strategy.should_retry(item.error, attempt)
                        ):
                            last_error = item.error
                            node_id = item.node_id or node_id
                            should_retry = True
                            break
                        yield item
                        return
                    first_token_yielded = True
                    yield item
            except Exception as e:
                if not first_token_yielded and self._strategy.should_retry(e, attempt):
                    last_error = e
                    should_retry = True
                else:
                    raise

            if not should_retry or last_error is None:
                return

            delay = self._strategy.get_backoff(attempt)
            logger.warning(
                "Retrying stream for node '%s' after %s (attempt %d, delay %.1fs)",
                node_id,
                type(last_error).__name__,
                attempt + 1,
                delay,
            )
            await self._emit_retry_event(ctx, last_error, attempt, delay, node_id)
            await asyncio.sleep(delay)
            attempt += 1

    @staticmethod
    async def _emit_retry_event(
        ctx: dict[str, Any] | None,
        error: Exception,
        attempt: int,
        delay: float,
        node_id: str,
    ) -> None:
        """Emit a CUSTOM retry event to EventStore if available.

        Args:
            ctx: Execution context dict (may contain event_store, session_id, superstep).
            error: The exception that triggered the retry.
            attempt: Current attempt number (0-based).
            delay: Backoff delay in seconds.
            node_id: The node being retried.
        """
        if ctx is None:
            return
        event_store = ctx.get("event_store")
        if event_store is None:
            return
        await event_store.append(
            Event(
                session_id=ctx.get("session_id", ""),
                superstep=ctx.get("superstep", 0),
                event_type=EventType.CUSTOM,
                node_id=node_id,
                payload={
                    "event_name": "RETRY",
                    "attempt": attempt + 1,
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                    "backoff_seconds": delay,
                },
            )
        )
