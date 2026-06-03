"""Tests for LLM circuit breaker (feature 1.3.10).

Covers:
- Prefix extraction from model names
- CircuitBreakerManager lazy creation and thread safety
- State transitions (CLOSED → OPEN → HALF_OPEN → CLOSED/OPEN)
- Single-probe HALF_OPEN with asyncio.Lock
- on_state_change callback
- No-breaker regression
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from hecate.services.llm.circuit_breaker import CircuitBreakerManager, _extract_prefix
from hecate.services.llm.service import LLMService
from hecate.services.validation.retry_policy import CircuitState

# ---------------------------------------------------------------------------
# 4.2 Prefix extraction
# ---------------------------------------------------------------------------


class TestExtractPrefix:
    """Tests for _extract_prefix."""

    def test_slash_prefix_openai(self) -> None:
        assert _extract_prefix("openai/gpt-4o") == "openai"

    def test_slash_prefix_anthropic(self) -> None:
        assert _extract_prefix("anthropic/claude-3.5-sonnet") == "anthropic"

    def test_slash_prefix_bedrock(self) -> None:
        assert _extract_prefix("bedrock/claude-3.5") == "bedrock"

    def test_slash_prefix_vertex(self) -> None:
        assert _extract_prefix("vertex_ai/gemini-pro") == "vertex_ai"

    def test_short_name_gpt(self) -> None:
        assert _extract_prefix("gpt-4o") == "openai"

    def test_short_name_gpt4(self) -> None:
        assert _extract_prefix("gpt-4") == "openai"

    def test_short_name_claude(self) -> None:
        assert _extract_prefix("claude-3.5-sonnet") == "anthropic"

    def test_short_name_gemini(self) -> None:
        assert _extract_prefix("gemini-pro") == "gemini"

    def test_short_name_deepseek(self) -> None:
        assert _extract_prefix("deepseek-chat") == "deepseek"

    def test_unknown_model(self) -> None:
        assert _extract_prefix("some-new-model") == "unknown"

    def test_empty_string(self) -> None:
        assert _extract_prefix("") == "unknown"


# ---------------------------------------------------------------------------
# 4.3 Lazy creation and thread safety
# ---------------------------------------------------------------------------


class TestCircuitBreakerManagerLazyCreation:
    """Tests for lazy breaker creation."""

    def test_lazy_creates_breaker(self) -> None:
        mgr = CircuitBreakerManager()
        assert "openai" not in mgr._breakers
        breaker = mgr.get_breaker("openai")
        assert "openai" in mgr._breakers
        assert breaker is not None

    def test_same_breaker_returned(self) -> None:
        mgr = CircuitBreakerManager()
        b1 = mgr.get_breaker("openai")
        b2 = mgr.get_breaker("openai")
        assert b1 is b2

    def test_different_prefixes_get_different_breakers(self) -> None:
        mgr = CircuitBreakerManager()
        b_openai = mgr.get_breaker("openai")
        b_anthropic = mgr.get_breaker("anthropic")
        assert b_openai is not b_anthropic

    async def test_concurrent_creation_no_duplicates(self) -> None:
        mgr = CircuitBreakerManager()
        results: list[object] = []

        async def create_breaker() -> None:
            b = await mgr.get_breaker_safe("openai")
            results.append(b)

        await asyncio.gather(*[create_breaker() for _ in range(10)])
        assert len(mgr._breakers) == 1
        # All results should be the same object
        assert all(r is results[0] for r in results)

    def test_custom_threshold_and_timeout(self) -> None:
        mgr = CircuitBreakerManager(failure_threshold=3, recovery_timeout=10.0)
        breaker = mgr.get_breaker("openai")
        assert breaker.failure_threshold == 3
        assert breaker.recovery_timeout == 10.0

    def test_probe_lock_created_with_breaker(self) -> None:
        mgr = CircuitBreakerManager()
        mgr.get_breaker("openai")
        assert "openai" in mgr._probe_locks


# ---------------------------------------------------------------------------
# 4.4 State transitions
# ---------------------------------------------------------------------------


class TestStateTransitions:
    """Tests for circuit breaker state machine."""

    def test_initial_state_is_closed(self) -> None:
        mgr = CircuitBreakerManager()
        breaker = mgr.get_breaker("openai")
        assert breaker.state == CircuitState.CLOSED

    def test_closed_to_open_after_threshold(self) -> None:
        mgr = CircuitBreakerManager(failure_threshold=3)
        for _ in range(3):
            mgr.record_failure("openai/gpt-4o")
        assert mgr.is_open("openai/gpt-4o")

    def test_not_open_below_threshold(self) -> None:
        mgr = CircuitBreakerManager(failure_threshold=5)
        for _ in range(4):
            mgr.record_failure("openai/gpt-4o")
        assert not mgr.is_open("openai/gpt-4o")

    def test_success_resets_failure_count(self) -> None:
        mgr = CircuitBreakerManager(failure_threshold=3)
        mgr.record_failure("openai/gpt-4o")
        mgr.record_failure("openai/gpt-4o")
        mgr.record_success("openai/gpt-4o")
        # Failure count reset, need 3 more to open
        mgr.record_failure("openai/gpt-4o")
        mgr.record_failure("openai/gpt-4o")
        assert not mgr.is_open("openai/gpt-4o")

    def test_open_to_half_open_after_timeout(self) -> None:
        mgr = CircuitBreakerManager(failure_threshold=1, recovery_timeout=0.01)
        mgr.record_failure("openai/gpt-4o")
        assert mgr.is_open("openai/gpt-4o")
        time.sleep(0.02)
        assert mgr.is_half_open("openai/gpt-4o")
        assert not mgr.is_open("openai/gpt-4o")

    def test_half_open_to_closed_on_success(self) -> None:
        mgr = CircuitBreakerManager(failure_threshold=1, recovery_timeout=0.01)
        mgr.record_failure("openai/gpt-4o")
        time.sleep(0.02)
        assert mgr.is_half_open("openai/gpt-4o")
        mgr.record_success("openai/gpt-4o")
        assert not mgr.is_half_open("openai/gpt-4o")
        assert not mgr.is_open("openai/gpt-4o")

    def test_half_open_to_open_on_failure(self) -> None:
        mgr = CircuitBreakerManager(failure_threshold=1, recovery_timeout=0.01)
        mgr.record_failure("openai/gpt-4o")
        time.sleep(0.02)
        assert mgr.is_half_open("openai/gpt-4o")
        mgr.record_failure("openai/gpt-4o")
        assert mgr.is_open("openai/gpt-4o")


# ---------------------------------------------------------------------------
# 4.5 Single-probe HALF_OPEN
# ---------------------------------------------------------------------------


class TestHalfOpenProbe:
    """Tests for single-probe HALF_OPEN behavior."""

    def test_acquire_probe_succeeds_when_free(self) -> None:
        mgr = CircuitBreakerManager(failure_threshold=1, recovery_timeout=0.01)
        mgr.record_failure("openai/gpt-4o")
        time.sleep(0.02)
        assert mgr.is_half_open("openai/gpt-4o")
        assert mgr.acquire_probe("openai/gpt-4o") is True

    def test_acquire_probe_fails_when_held(self) -> None:
        mgr = CircuitBreakerManager(failure_threshold=1, recovery_timeout=0.01)
        mgr.record_failure("openai/gpt-4o")
        time.sleep(0.02)
        assert mgr.acquire_probe("openai/gpt-4o") is True
        assert mgr.acquire_probe("openai/gpt-4o") is False

    def test_release_allows_new_probe(self) -> None:
        mgr = CircuitBreakerManager(failure_threshold=1, recovery_timeout=0.01)
        mgr.record_failure("openai/gpt-4o")
        time.sleep(0.02)
        assert mgr.acquire_probe("openai/gpt-4o") is True
        mgr.release_probe("openai/gpt-4o")
        assert mgr.acquire_probe("openai/gpt-4o") is True
        mgr.release_probe("openai/gpt-4o")


# ---------------------------------------------------------------------------
# 4.9 on_state_change callback
# ---------------------------------------------------------------------------


class TestStateChangeCallback:
    """Tests for on_state_change callback."""

    def test_callback_invoked_on_state_change(self) -> None:
        changes: list[tuple[str, str, str]] = []
        mgr = CircuitBreakerManager(
            failure_threshold=2,
            on_state_change=lambda prefix, old, new: changes.append((prefix, old.value, new.value)),
        )
        mgr.record_failure("openai/gpt-4o")
        mgr.record_failure("openai/gpt-4o")
        assert len(changes) == 1
        assert changes[0] == ("openai", "closed", "open")

    def test_callback_not_invoked_without_change(self) -> None:
        changes: list[tuple[str, str, str]] = []
        mgr = CircuitBreakerManager(
            failure_threshold=5,
            on_state_change=lambda prefix, old, new: changes.append((prefix, old.value, new.value)),
        )
        mgr.record_failure("openai/gpt-4o")
        assert len(changes) == 0

    def test_no_callback_when_none(self) -> None:
        mgr = CircuitBreakerManager(failure_threshold=1)
        mgr.record_failure("openai/gpt-4o")
        # No exception raised

    def test_callback_exception_does_not_propagate(self) -> None:
        def bad_callback(prefix: str, old: CircuitState, new: CircuitState) -> None:
            raise ValueError("callback error")

        mgr = CircuitBreakerManager(failure_threshold=1, on_state_change=bad_callback)
        mgr.record_failure("openai/gpt-4o")
        # No exception raised


# ---------------------------------------------------------------------------
# 4.10 No-breaker regression
# ---------------------------------------------------------------------------


class TestNoBreakerRegression:
    """Tests that LLMService works identically without a breaker."""

    async def test_chat_without_breaker(self) -> None:
        service = LLMService(fallback_models=["anthropic/claude-3.5"])

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "hello"
        mock_response.choices[0].message.tool_calls = None
        mock_response.model = "openai/gpt-4o"
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.usage.total_tokens = 15
        mock_response.choices[0].finish_reason = "stop"

        with patch("hecate.services.llm.service._get_litellm") as mock_litellm:
            mock_litellm.return_value.acompletion = AsyncMock(return_value=mock_response)
            result = await service.chat([], model="openai/gpt-4o")

        assert result.content == "hello"
        assert result.model == "openai/gpt-4o"

    async def test_fallback_without_breaker(self) -> None:
        service = LLMService(fallback_models=["anthropic/claude-3.5"])

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "fallback"
        mock_response.choices[0].message.tool_calls = None
        mock_response.model = "anthropic/claude-3.5"
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.usage.total_tokens = 15
        mock_response.choices[0].finish_reason = "stop"

        call_count = 0

        async def side_effect(**kwargs: object) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("primary failed")
            return mock_response

        with patch("hecate.services.llm.service._get_litellm") as mock_litellm:
            mock_litellm.return_value.acompletion = side_effect
            result = await service.chat([], model="openai/gpt-4o")

        assert result.content == "fallback"
        assert call_count == 2
