"""Tests wiring the dormant policy trio into LLMService (phase-4 follow-ups B1/B2).

CircuitBreaker wraps chat/chat_stream's try/except; GrayRelease and ABTest
compete in _resolve_model via routing_config keys. All managers default to
None (disabled) — every test here constructs LLMService explicitly with the
manager under test.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hecate_llm.ab_testing import ABTestConfig, ABTestManager
from hecate_llm.circuit_breaker import CircuitBreakerManager
from hecate_llm.gray_release import GrayReleaseConfig, GrayReleaseManager
from hecate_llm.service import LLMService


def _mock_response(content: str = "ok", model: str = "gpt-4o") -> MagicMock:
    choice = MagicMock()
    choice.message.content = content
    choice.message.tool_calls = None
    choice.finish_reason = "stop"
    response = MagicMock()
    response.choices = [choice]
    response.model = model
    usage = MagicMock()
    usage.prompt_tokens = 1
    usage.completion_tokens = 1
    usage.total_tokens = 2
    response.usage = usage
    return response


# ---------------------------------------------------------------------------
# B1: CircuitBreaker wiring
# ---------------------------------------------------------------------------


class TestCircuitBreakerWiring:
    async def test_chat_success_records_success(self) -> None:
        breaker = CircuitBreakerManager()
        service = LLMService(circuit_breaker=breaker)

        with patch("hecate_llm.service._get_litellm") as mock_get:
            mock_get.return_value.acompletion = AsyncMock(return_value=_mock_response())
            await service.chat(messages=[{"role": "user", "content": "hi"}], model="gpt-4o")

        assert not breaker.is_open("gpt-4o")

    async def test_chat_failure_records_failure_and_falls_back(self) -> None:
        breaker = CircuitBreakerManager()
        service = LLMService(fallback_models=["claude-3-5-sonnet"], circuit_breaker=breaker)

        with patch("hecate_llm.service._get_litellm") as mock_get:
            # Primary model fails; the fallback model succeeds.
            async def _acompletion(**kwargs: Any) -> MagicMock:
                if kwargs["model"] == "gpt-4o":
                    raise RuntimeError("primary down")
                return _mock_response(content="fallback", model=kwargs["model"])

            mock_get.return_value.acompletion = AsyncMock(side_effect=_acompletion)
            response = await service.chat(messages=[{"role": "user", "content": "hi"}], model="gpt-4o")

        assert response.content == "fallback"
        assert response.model == "claude-3-5-sonnet"

    async def test_chat_open_circuit_short_circuits_to_fallback(self) -> None:
        """When the breaker is OPEN, chat skips the doomed call entirely."""
        breaker = CircuitBreakerManager(failure_threshold=1)
        service = LLMService(fallback_models=["claude-3-5-sonnet"], circuit_breaker=breaker)

        # Trip the breaker for the gpt-4o prefix.
        for _ in range(3):
            breaker.record_failure("gpt-4o")
        assert breaker.is_open("gpt-4o")

        with patch("hecate_llm.service._get_litellm") as mock_get:
            mock_get.return_value.acompletion = AsyncMock(
                return_value=_mock_response(content="recovered", model="claude-3-5-sonnet")
            )
            response = await service.chat(messages=[{"role": "user", "content": "hi"}], model="gpt-4o")

        # The primary model was never invoked.
        call_models = [c.kwargs.get("model") for c in mock_get.return_value.acompletion.call_args_list]
        assert "gpt-4o" not in call_models
        assert response.model == "claude-3-5-sonnet"

    async def test_chat_open_circuit_no_fallback_raises(self) -> None:
        breaker = CircuitBreakerManager(failure_threshold=2)
        service = LLMService(circuit_breaker=breaker)
        breaker.record_failure("gpt-4o")
        breaker.record_failure("gpt-4o")
        assert breaker.is_open("gpt-4o")

        with pytest.raises(RuntimeError, match="Circuit open"):
            await service.chat(messages=[{"role": "user", "content": "hi"}], model="gpt-4o")

    async def test_chat_stream_failure_records_failure(self) -> None:
        breaker = CircuitBreakerManager()
        service = LLMService(circuit_breaker=breaker)

        async def _failing_stream(**kwargs: Any) -> Any:
            raise RuntimeError("stream broke")
            yield  # pragma: no cover

        with patch("hecate_llm.service._get_litellm") as mock_get:
            mock_get.return_value.acompletion = AsyncMock(return_value=_failing_stream())
            with pytest.raises(RuntimeError, match="stream broke"):
                async for _chunk in service.chat_stream(messages=[{"role": "user", "content": "hi"}], model="gpt-4o"):
                    pass  # pragma: no cover — the first iteration raises

        # The failure was recorded on the breaker before propagating.
        assert "gpt-4o" in repr(breaker) or breaker._breakers  # state tracked
        assert any("openai" in p for p in breaker._breakers)

    async def test_without_breaker_behavior_unchanged(self) -> None:
        """Default None breaker keeps the historical no-breaker semantics."""
        service = LLMService()
        assert service._breaker is None

        with patch("hecate_llm.service._get_litellm") as mock_get:
            mock_get.return_value.acompletion = AsyncMock(return_value=_mock_response())
            response = await service.chat(messages=[{"role": "user", "content": "hi"}], model="gpt-4o")
        assert response.content == "ok"


# ---------------------------------------------------------------------------
# B2: GrayRelease + ABTest wiring in _resolve_model
# ---------------------------------------------------------------------------


def _make_gray_release() -> GrayReleaseManager:
    manager = GrayReleaseManager()
    manager.create_release(
        GrayReleaseConfig(
            release_name="release_name",
            models={"gpt-4o": 80, "gpt-4o-mini": 20},
        ),
    )
    return manager


def _make_ab_test() -> ABTestManager:
    manager = ABTestManager()
    manager.create_test(
        ABTestConfig(
            test_name="test_name",
            model_a="gpt-4o",
            model_b="gpt-4o-mini",
            traffic_split=0.5,
        ),
    )
    return manager


class TestGrayReleaseWiring:
    def test_resolve_model_via_gray_release(self) -> None:
        service = LLMService(gray_release=_make_gray_release())
        resolved = service._resolve_model(None, {"release_name": "release_name", "context_key": "user-1"})
        assert resolved in {"gpt-4o", "gpt-4o-mini"}

    def test_context_key_deterministic(self) -> None:
        service = LLMService(gray_release=_make_gray_release())
        first = service._resolve_model(None, {"release_name": "release_name", "context_key": "user-42"})
        second = service._resolve_model(None, {"release_name": "release_name", "context_key": "user-42"})
        assert first == second

    def test_release_and_test_mutually_exclusive(self) -> None:
        service = LLMService(gray_release=_make_gray_release(), ab_testing=_make_ab_test())
        with pytest.raises(ValueError, match="release_name and test_name"):
            service._resolve_model(None, {"release_name": "r", "test_name": "t"})


class TestABTestWiring:
    def test_resolve_model_via_ab_test(self) -> None:
        service = LLMService(ab_testing=_make_ab_test())
        resolved = service._resolve_model(None, {"test_name": "test_name", "context_key": "user-7"})
        assert resolved in {"gpt-4o", "gpt-4o-mini"}

    def test_ab_test_deterministic_per_context(self) -> None:
        service = LLMService(ab_testing=_make_ab_test())
        first = service._resolve_model(None, {"test_name": "test_name", "context_key": "user-99"})
        second = service._resolve_model(None, {"test_name": "test_name", "context_key": "user-99"})
        assert first == second

    def test_none_managers_fall_through(self) -> None:
        """Without managers wired, release_name/test_name keys are ignored."""
        service = LLMService(fallback_models=["gpt-4o-mini"])
        resolved = service._resolve_model(None, {"release_name": "whatever"})
        assert resolved == "gpt-4o-mini"

    async def test_chat_routes_through_ab_test_selection(self) -> None:
        service = LLMService(ab_testing=_make_ab_test())
        with patch("hecate_llm.service._get_litellm") as mock_get:
            mock_get.return_value.acompletion = AsyncMock(return_value=_mock_response(model="gpt-4o"))
            await service.chat(
                messages=[{"role": "user", "content": "hi"}],
                routing_config={"test_name": "test_name", "context_key": "user-7"},
            )
        called_model = mock_get.return_value.acompletion.call_args.kwargs["model"]
        assert called_model in {"gpt-4o", "gpt-4o-mini"}
