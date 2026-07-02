"""Tests for the unified exception hierarchy (1.3.5g)."""

from __future__ import annotations

import pytest

from hecate.engine.channel import ChannelManager
from hecate.engine.errors import (
    ChannelError,
    ChannelNotFoundError,
    EngineError,
    ErrorCategory,
    GraphValidationError,
    GuardrailBlockedError,
    HecateError,
    MaxSuperstepsError,
    SecurityError,
)
from hecate.engine.types import ChannelDef, ChannelType
from hecate.services.validation.retry_policy import ErrorClassifier

# --- Exception hierarchy tests ---


class TestHecateErrorHierarchy:
    def test_hecate_error_is_exception(self) -> None:
        assert issubclass(HecateError, Exception)

    def test_engine_error_is_hecate_error(self) -> None:
        assert issubclass(EngineError, HecateError)

    def test_channel_error_is_hecate_error(self) -> None:
        assert issubclass(ChannelError, HecateError)

    def test_security_error_is_hecate_error(self) -> None:
        assert issubclass(SecurityError, HecateError)

    def test_max_supersteps_error_is_engine_error(self) -> None:
        assert issubclass(MaxSuperstepsError, EngineError)
        exc = MaxSuperstepsError("test", superstep=100)
        assert exc.superstep == 100
        assert isinstance(exc, HecateError)

    def test_channel_not_found_is_key_error_and_channel_error(self) -> None:
        assert issubclass(ChannelNotFoundError, KeyError)
        assert issubclass(ChannelNotFoundError, ChannelError)
        exc = ChannelNotFoundError("messages")
        assert exc.channel_name == "messages"
        assert isinstance(exc, KeyError)

    def test_guardrail_blocked_is_security_error(self) -> None:
        assert issubclass(GuardrailBlockedError, SecurityError)
        exc = GuardrailBlockedError("blocked by policy")
        assert exc.reason == "blocked by policy"

    def test_graph_validation_error_is_engine_error(self) -> None:
        assert issubclass(GraphValidationError, EngineError)
        exc = GraphValidationError("invalid", field="nodes.x.config")
        assert exc.field == "nodes.x.config"

    def test_graph_validation_error_still_catchable_as_exception(self) -> None:
        with pytest.raises(HecateError):
            raise GraphValidationError("test")

    def test_graph_validation_error_catchable_as_engine_error(self) -> None:
        with pytest.raises(EngineError):
            raise GraphValidationError("test")

    def test_channel_not_found_catchable_as_key_error(self) -> None:
        with pytest.raises(KeyError):
            raise ChannelNotFoundError("test")


# --- ErrorCategory enum tests ---


class TestErrorCategory:
    def test_string_comparison(self) -> None:
        assert ErrorCategory.LLM_RATE_LIMIT == "llm_rate_limit"
        assert ErrorCategory.ENGINE == "engine"

    def test_all_members_present(self) -> None:
        expected = {
            "llm_rate_limit",
            "llm_auth",
            "llm_timeout",
            "llm_context_window",
            "tool_timeout",
            "tool_not_found",
            "tool_execution",
            "engine",
            "security",
            "channel",
            "unknown",
        }
        actual = {member.value for member in ErrorCategory.__members__.values()}
        assert actual == expected

    def test_is_str_enum(self) -> None:
        assert isinstance(ErrorCategory.ENGINE, str)


# --- ErrorClassifier tests ---


class TestErrorClassifierClassify:
    def test_classify_engine_error(self) -> None:
        classifier = ErrorClassifier()
        assert classifier.classify(MaxSuperstepsError("test")) == ErrorCategory.ENGINE

    def test_classify_channel_error(self) -> None:
        classifier = ErrorClassifier()
        assert classifier.classify(ChannelNotFoundError("x")) == ErrorCategory.CHANNEL

    def test_classify_security_error(self) -> None:
        classifier = ErrorClassifier()
        assert classifier.classify(GuardrailBlockedError("blocked")) == ErrorCategory.SECURITY

    def test_classify_graph_validation_error_as_engine(self) -> None:
        classifier = ErrorClassifier()
        assert classifier.classify(GraphValidationError("invalid")) == ErrorCategory.ENGINE

    def test_classify_string_fallback_timeout(self) -> None:
        classifier = ErrorClassifier()
        assert classifier.classify(ValueError("connection timeout")) == ErrorCategory.LLM_TIMEOUT

    def test_classify_string_fallback_rate_limit(self) -> None:
        classifier = ErrorClassifier()
        assert classifier.classify(RuntimeError("429 rate limit exceeded")) == ErrorCategory.LLM_RATE_LIMIT

    def test_classify_string_fallback_unrecognized(self) -> None:
        classifier = ErrorClassifier()
        assert classifier.classify(ValueError("something random")) == ErrorCategory.UNKNOWN


class TestErrorClassifierRetryable:
    def test_is_retryable_string_backward_compat(self) -> None:
        classifier = ErrorClassifier()
        assert classifier.is_retryable("timeout error") is True
        assert classifier.is_retryable("unauthorized access") is False
        assert classifier.is_retryable("unknown error") is True

    def test_is_retryable_exception_retryable(self) -> None:
        classifier = ErrorClassifier()
        # LLM_RATE_LIMIT is retryable
        exc = RuntimeError("429 rate limit")
        assert classifier.is_retryable_exception(exc) is True

    def test_is_retryable_exception_non_retryable(self) -> None:
        classifier = ErrorClassifier()
        # ENGINE errors are not retryable
        assert classifier.is_retryable_exception(MaxSuperstepsError("test")) is False

    def test_is_retryable_exception_unknown_falls_back(self) -> None:
        classifier = ErrorClassifier()
        # Unknown errors fall back to string matching
        assert classifier.is_retryable_exception(ValueError("timeout")) is True


# --- Channel integration test ---


class TestChannelNotFoundError:
    def test_read_unregistered_channel_raises_channel_not_found(self) -> None:
        cm = ChannelManager()
        cm.register("existing", ChannelDef(type=ChannelType.LAST_VALUE))
        with pytest.raises(ChannelNotFoundError) as exc_info:
            cm.read("nonexistent")
        assert exc_info.value.channel_name == "nonexistent"

    def test_channel_not_found_still_catchable_as_key_error(self) -> None:
        cm = ChannelManager()
        with pytest.raises(KeyError):
            cm.read("nonexistent")
