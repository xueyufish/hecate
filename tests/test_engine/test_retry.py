"""Tests for engine retry strategy and executor (1.3.5h Framework-Level Auto-Retry)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from hecate.engine.retry import NoRetryStrategy, RetryExecutor, RetryStrategy
from hecate.engine.types import WorkerResult
from hecate.services.validation.retry_policy import (
    DefaultRetryStrategy,
    ErrorClassifier,
)


class _StubError(Exception):
    pass


class _AlwaysRetryStrategy(RetryStrategy):
    def should_retry(self, error: Exception, attempt: int) -> bool:
        return attempt < 2

    def get_backoff(self, attempt: int) -> float:
        return 0.001


class _NeverRetryStrategy(RetryStrategy):
    def should_retry(self, error: Exception, attempt: int) -> bool:
        return False

    def get_backoff(self, attempt: int) -> float:
        return 0.0


async def _succeed_immediately(*args: Any, **kwargs: Any) -> WorkerResult:
    return WorkerResult(node_id="test", channel_updates={"out": "ok"})


async def _fail_once_then_succeed(*args: Any, **kwargs: Any) -> WorkerResult:
    if not hasattr(_fail_once_then_succeed, "_called"):
        _fail_once_then_succeed._called = True  # type: ignore[attr-defined]
        return WorkerResult(node_id="test", error=_StubError("transient"))
    return WorkerResult(node_id="test", channel_updates={"out": "ok"})


async def _always_fail(*args: Any, **kwargs: Any) -> WorkerResult:
    return WorkerResult(node_id="test", error=_StubError("permanent"))


class TestRetryStrategyABC:
    def test_retry_strategy_not_instantiable(self) -> None:
        with pytest.raises(TypeError):
            RetryStrategy()  # type: ignore[abstract]

    def test_custom_implementation_works(self) -> None:
        strategy = _AlwaysRetryStrategy()
        assert strategy.should_retry(_StubError("x"), 0) is True

    def test_with_config_default_returns_self(self) -> None:
        strategy = _AlwaysRetryStrategy()
        result = strategy.with_config(max_attempts=5)
        assert result is strategy


class TestNoRetryStrategy:
    def test_should_retry_always_false(self) -> None:
        strategy = NoRetryStrategy()
        assert strategy.should_retry(Exception("anything"), 0) is False
        assert strategy.should_retry(Exception("anything"), 99) is False

    def test_get_backoff_always_zero(self) -> None:
        strategy = NoRetryStrategy()
        assert strategy.get_backoff(0) == 0.0
        assert strategy.get_backoff(10) == 0.0


class TestRetryExecutorNonStreaming:
    async def test_success_first_attempt(self) -> None:
        executor = RetryExecutor(_AlwaysRetryStrategy())
        result = await executor.execute(_succeed_immediately)
        assert result.error is None
        assert result.channel_updates == {"out": "ok"}

    async def test_retryable_then_success(self) -> None:
        if hasattr(_fail_once_then_succeed, "_called"):
            delattr(_fail_once_then_succeed, "_called")
        executor = RetryExecutor(_AlwaysRetryStrategy())
        result = await executor.execute(_fail_once_then_succeed)
        assert result.error is None
        assert result.channel_updates == {"out": "ok"}

    async def test_non_retryable_propagates(self) -> None:
        executor = RetryExecutor(_NeverRetryStrategy())
        result = await executor.execute(_always_fail)
        assert result.error is not None
        assert isinstance(result.error, _StubError)

    async def test_max_attempts_exhausted(self) -> None:
        executor = RetryExecutor(_AlwaysRetryStrategy())
        result = await executor.execute(_always_fail)
        assert result.error is not None

    async def test_default_no_retry_strategy(self) -> None:
        executor = RetryExecutor()
        result = await executor.execute(_always_fail)
        assert result.error is not None


class TestRetryExecutorStreaming:
    @staticmethod
    async def _stream_success(*args: Any, **kwargs: Any) -> AsyncGenerator[dict[str, Any] | WorkerResult, None]:
        yield {"content": "hello"}
        yield {"content": " world"}
        yield WorkerResult(node_id="test", channel_updates={"out": "hello world"})

    @staticmethod
    async def _stream_fail_before_first_token(
        *args: Any, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any] | WorkerResult, None]:
        yield WorkerResult(node_id="test", error=_StubError("connection"))
        return

    @staticmethod
    async def _stream_fail_after_first_token(
        *args: Any, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any] | WorkerResult, None]:
        yield {"content": "partial"}
        yield WorkerResult(node_id="test", error=_StubError("mid-stream"))
        return

    @staticmethod
    async def _stream_raise_before_first_token(
        *args: Any, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any] | WorkerResult, None]:
        raise _StubError("connection error")
        yield  # type: ignore[unreachable]

    @staticmethod
    async def _stream_raise_after_first_token(
        *args: Any, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any] | WorkerResult, None]:
        yield {"content": "partial"}
        raise _StubError("mid-stream error")
        yield  # type: ignore[unreachable]

    _stream_call_count = 0

    @staticmethod
    async def _stream_fail_then_succeed(
        *args: Any, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any] | WorkerResult, None]:
        TestRetryExecutorStreaming._stream_call_count += 1
        if TestRetryExecutorStreaming._stream_call_count == 1:
            raise _StubError("first attempt fails")
        yield {"content": "success"}
        yield WorkerResult(node_id="test", channel_updates={"out": "success"})

    async def test_stream_success_no_retry(self) -> None:
        executor = RetryExecutor(_AlwaysRetryStrategy())
        items = [item async for item in executor.execute_stream(self._stream_success)]
        assert len(items) == 3
        assert items[0] == {"content": "hello"}
        assert items[1] == {"content": " world"}
        assert isinstance(items[2], WorkerResult)

    async def test_stream_workerresult_error_before_token_retried(self) -> None:
        call_count = 0

        async def stream_func(*args: Any, **kwargs: Any) -> AsyncGenerator[dict[str, Any] | WorkerResult, None]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield WorkerResult(node_id="test", error=_StubError("fail"))
                return
            yield {"content": "ok"}
            yield WorkerResult(node_id="test", channel_updates={"out": "ok"})

        executor = RetryExecutor(_AlwaysRetryStrategy())
        items = [item async for item in executor.execute_stream(stream_func)]
        assert call_count == 2
        assert len(items) == 2

    async def test_stream_error_after_first_token_no_retry(self) -> None:
        executor = RetryExecutor(_AlwaysRetryStrategy())
        items: list[Any] = []
        with pytest.raises(_StubError):
            async for item in executor.execute_stream(self._stream_raise_after_first_token):
                items.append(item)
        assert len(items) == 1
        assert items[0] == {"content": "partial"}

    async def test_stream_workerresult_error_after_token_no_retry(self) -> None:
        executor = RetryExecutor(_AlwaysRetryStrategy())
        items = [item async for item in executor.execute_stream(self._stream_fail_after_first_token)]
        assert len(items) == 2
        assert items[0] == {"content": "partial"}
        assert isinstance(items[1], WorkerResult)
        assert items[1].error is not None

    async def test_stream_raise_before_token_retried(self) -> None:
        TestRetryExecutorStreaming._stream_call_count = 0
        executor = RetryExecutor(_AlwaysRetryStrategy())
        items = [item async for item in executor.execute_stream(self._stream_fail_then_succeed)]
        assert TestRetryExecutorStreaming._stream_call_count == 2
        assert len(items) == 2

    async def test_stream_no_duplicate_tokens_on_retry(self) -> None:
        call_count = 0

        async def stream_func(*args: Any, **kwargs: Any) -> AsyncGenerator[dict[str, Any] | WorkerResult, None]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield {"content": "should-not-appear"}
                raise _StubError("fail after token")
            yield {"content": "real"}
            yield WorkerResult(node_id="test", channel_updates={"out": "real"})

        executor = RetryExecutor(_AlwaysRetryStrategy())
        with pytest.raises(_StubError):
            async for _item in executor.execute_stream(stream_func):
                pass


class TestRetryExecutorEventStore:
    async def test_retry_event_emitted(self) -> None:
        mock_store = MagicMock()
        mock_store.append = AsyncMock()

        ctx: dict[str, Any] = {
            "session_id": "test-session",
            "superstep": 1,
            "event_store": mock_store,
        }

        call_count = 0

        async def func(*args: Any, **kwargs: Any) -> WorkerResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return WorkerResult(node_id="node1", error=_StubError("fail"))
            return WorkerResult(node_id="node1", channel_updates={"ok": True})

        executor = RetryExecutor(_AlwaysRetryStrategy())
        await executor.execute(func, execution_context=ctx)
        assert mock_store.append.call_count == 1

    async def test_no_event_when_no_event_store(self) -> None:
        ctx: dict[str, Any] = {"session_id": "test", "superstep": 0}

        call_count = 0

        async def func(*args: Any, **kwargs: Any) -> WorkerResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return WorkerResult(node_id="n", error=_StubError("x"))
            return WorkerResult(node_id="n")

        executor = RetryExecutor(_AlwaysRetryStrategy())
        result = await executor.execute(func, execution_context=ctx)
        assert result.error is None
        assert call_count == 2


class TestDefaultRetryStrategy:
    def test_should_retry_rate_limit(self) -> None:
        strategy = DefaultRetryStrategy(max_attempts=3)

        class FakeRateLimitError(Exception):
            pass

        strategy._classifier = MagicMock(spec=ErrorClassifier)
        strategy._classifier.is_retryable_exception = MagicMock(return_value=True)
        assert strategy.should_retry(FakeRateLimitError(), 0) is True

    def test_should_not_retry_auth_error(self) -> None:
        strategy = DefaultRetryStrategy(max_attempts=3)

        class FakeAuthError(Exception):
            pass

        strategy._classifier = MagicMock(spec=ErrorClassifier)
        strategy._classifier.is_retryable_exception = MagicMock(return_value=False)
        assert strategy.should_retry(FakeAuthError(), 0) is False

    def test_should_not_retry_when_max_exceeded(self) -> None:
        strategy = DefaultRetryStrategy(max_attempts=2)
        assert strategy.should_retry(Exception("timeout"), 2) is False
        assert strategy.should_retry(Exception("timeout"), 3) is False

    def test_backoff_in_jitter_range(self) -> None:
        strategy = DefaultRetryStrategy(max_attempts=5, base_delay=1.0, multiplier=2.0, max_delay=30.0)
        for _ in range(100):
            delay = strategy.get_backoff(2)
            base = min(1.0 * (2.0**2), 30.0)
            assert base * 0.5 <= delay <= base * 1.5

    def test_backoff_respects_max_delay(self) -> None:
        strategy = DefaultRetryStrategy(base_delay=1.0, multiplier=2.0, max_delay=5.0)
        for _ in range(100):
            delay = strategy.get_backoff(20)
            assert delay <= 5.0 * 1.5

    def test_with_config_creates_new_instance(self) -> None:
        strategy = DefaultRetryStrategy(max_attempts=3, base_delay=1.0)
        new = strategy.with_config(max_attempts=5)
        assert new is not strategy
        assert new.max_attempts == 5
        assert new.base_delay == 1.0

    def test_with_config_preserves_unmodified_fields(self) -> None:
        strategy = DefaultRetryStrategy(max_attempts=3, base_delay=2.0, max_delay=60.0, multiplier=3.0)
        new = strategy.with_config(max_attempts=10)
        assert new.base_delay == 2.0
        assert new.max_delay == 60.0
        assert new.multiplier == 3.0
