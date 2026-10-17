"""Unified exception hierarchy for the Hecate execution engine.

Provides ``HecateError`` as the common base for all Hecate-specific exceptions,
plus three domain categories (EngineError, ChannelError, SecurityError) with
typed subtypes and an ``ErrorCategory`` enum for semantic error classification.

Design reference: 10-platform research (OpenAI SDK, LiteLLM, LangChain,
LangGraph, Google ADK, IBM watsonx, Salesforce, Huawei, AutoGen, CrewAI).
"""

from __future__ import annotations

from enum import StrEnum

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class HecateError(Exception):
    """Base for all Hecate engine errors."""


# ---------------------------------------------------------------------------
# Engine errors
# ---------------------------------------------------------------------------


class EngineError(HecateError):
    """Engine runtime errors."""


class ExecutionStoppedError(EngineError):
    """A terminal execution condition that must never be retried."""

    terminal_execution = True

    def __init__(self, reason_category: str, detail: str, action_ref: str | None = None) -> None:
        self.reason_category = reason_category
        self.detail = detail
        self.action_ref = action_ref
        super().__init__(f"{reason_category}: {detail}")


class MaxSuperstepsError(EngineError):
    """Graph execution exceeded the configured ``max_supersteps`` limit."""

    def __init__(self, message: str, *, superstep: int | None = None) -> None:
        self.superstep = superstep
        super().__init__(message)


class ApprovalWaitSignal(BaseException):
    """A protected action must park the durable task for a human decision.

    Raised by the durable-wait approval callback when a policy-required
    approval has no synchronous answerer. Derives from ``BaseException``
    (same family as ``CancelledError``/``GeneratorExit``) so the tool loop's
    ``except Exception`` upgrades (unknown receipt → ``needs_review``) and
    the retry executor cannot rewrite or retry it: the signal must escape
    the engine untouched and reach the dispatch boundary, which converts it
    into a durable ``WAITING_APPROVAL`` park instead of a failed terminal.
    """

    def __init__(
        self,
        tool_name: str,
        arguments: dict | None = None,
        risk_level: str = "",
        context: dict | None = None,
    ) -> None:
        self.tool_name = tool_name
        self.arguments = dict(arguments or {})
        self.risk_level = risk_level
        self.context = dict(context or {})
        super().__init__(f"tool '{tool_name}' waits for a durable approval decision")


class GraphValidationError(EngineError):
    """Raised when a graph definition fails schema or structural validation.

    Attributes:
        field: Dotted JSON path pointing to the invalid element (e.g.
            ``"nodes.guard.config.model"``).  None when the error applies to
            the entire document.
    """

    def __init__(self, message: str, *, field: str | None = None) -> None:
        self.field = field
        super().__init__(message)


# ---------------------------------------------------------------------------
# Channel errors
# ---------------------------------------------------------------------------


class ChannelError(HecateError):
    """Channel operation errors."""


class ChannelNotFoundError(KeyError, ChannelError):
    """Raised when a channel is read but not registered.

    Inherits from both ``KeyError`` (backward compatibility) and
    ``ChannelError`` so that ``except KeyError`` and ``except ChannelError``
    both catch this exception.
    """

    def __init__(self, name: str) -> None:
        self.channel_name = name
        super().__init__(f"Channel '{name}' not registered")


# ---------------------------------------------------------------------------
# Security errors
# ---------------------------------------------------------------------------


class SecurityError(HecateError):
    """Security / guardrail errors."""


class GuardrailBlockedError(SecurityError):
    """Raised when a guardrail hook blocks a request.

    Coexists with the return-based ``GuardrailResult(action=BLOCK)`` pattern.
    Use this exception for code paths that prefer exception-based control flow.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


# ---------------------------------------------------------------------------
# Error category enum (for classification / retry decisions)
# ---------------------------------------------------------------------------


class ErrorCategory(StrEnum):
    """Semantic error category for classification and retry decisions.

    Used by ``ErrorClassifier`` to map exceptions (via isinstance or string
    matching) to a known category.
    """

    LLM_RATE_LIMIT = "llm_rate_limit"
    LLM_AUTH = "llm_auth"
    LLM_TIMEOUT = "llm_timeout"
    LLM_CONTEXT_WINDOW = "llm_context_window"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_NOT_FOUND = "tool_not_found"
    TOOL_EXECUTION = "tool_execution"
    ENGINE = "engine"
    SECURITY = "security"
    CHANNEL = "channel"
    UNKNOWN = "unknown"
