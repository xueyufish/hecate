"""Pluggable guardrail hooks for agent execution safety and compliance.

Provides four independent hook types, each focused on a single interception
point in the agent execution lifecycle:

- ``PreLLMHook``  — intercept before sending messages to the LLM
- ``PostLLMHook`` — intercept after receiving the LLM response
- ``PreToolHook`` — intercept before executing a tool
- ``PostToolHook`` — intercept after a tool has been executed

Each hook returns a ``GuardrailResult`` with an allow, block, or sanitize
decision.  SANITIZE enables in-flight data transformation (e.g., PII masking)
while allowing execution to continue.

Design rationale (composition over inheritance):
Each hook type is a separate ABC so implementers only write the hooks they
need.  A ``NoOpPreLLMHook`` / ``NoOpPostLLMHook`` / ``NoOpPreToolHook`` /
``NoOpPostToolHook`` are provided as pass-through defaults.

Deferred to P3:
- ``check_cost_ceiling`` hook — deferred to BudgetGovernance (feature 4.1a)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class GuardrailAction(StrEnum):
    """Actions a guardrail hook can return.

    - ALLOW: execution continues unchanged
    - BLOCK: execution halts with a reason string
    - SANITIZE: execution continues with modified data from modified_data field
    """

    ALLOW = "allow"
    BLOCK = "block"
    SANITIZE = "sanitize"


@dataclass
class GuardrailResult:
    """Structured return value from a guardrail hook invocation.

    Attributes:
        action: The guardrail decision (allow, block, or sanitize).
        reason: Human-readable explanation when action is BLOCK; empty for ALLOW.
        modified_data: Transformed data when action is SANITIZE; None otherwise.
    """

    action: GuardrailAction = GuardrailAction.ALLOW
    reason: str = ""
    modified_data: dict | None = None


# ---------------------------------------------------------------------------
# Pre-LLM Hook
# ---------------------------------------------------------------------------


class PreLLMHook(ABC):
    """Hook that intercepts before sending messages to the LLM.

    Use cases: prompt injection detection, PII redaction, content policy
    enforcement.
    """

    @abstractmethod
    async def on_pre_llm_call(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None,
    ) -> GuardrailResult:
        """Intercept before sending messages to the LLM.

        Args:
            messages: Conversation messages about to be sent.
            model: The LLM model identifier.
            tools: Available tool definitions, or None.

        Returns:
            GuardrailResult with allow or block decision.
        """
        ...


class NoOpPreLLMHook(PreLLMHook):
    """Pass-through pre-LLM hook that allows all calls."""

    async def on_pre_llm_call(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None,
    ) -> GuardrailResult:
        """Allow all LLM calls without inspection.

        Args:
            messages: Conversation messages about to be sent.
            model: The LLM model identifier.
            tools: Available tool definitions, or None.

        Returns:
            GuardrailResult with action=ALLOW.
        """
        return GuardrailResult(action=GuardrailAction.ALLOW)


# ---------------------------------------------------------------------------
# Post-LLM Hook
# ---------------------------------------------------------------------------


class PostLLMHook(ABC):
    """Hook that intercepts after receiving the LLM response.

    Use cases: output toxicity detection, sensitive data masking.
    """

    @abstractmethod
    async def on_post_llm_call(
        self,
        response: dict,
        messages: list[dict],
    ) -> GuardrailResult:
        """Intercept after receiving the LLM response.

        Args:
            response: The raw LLM response dict.
            messages: The messages that were sent.

        Returns:
            GuardrailResult with allow or block decision.
        """
        ...


class NoOpPostLLMHook(PostLLMHook):
    """Pass-through post-LLM hook that allows all responses."""

    async def on_post_llm_call(
        self,
        response: dict,
        messages: list[dict],
    ) -> GuardrailResult:
        """Allow all LLM responses without inspection.

        Args:
            response: The raw LLM response dict.
            messages: The messages that were sent.

        Returns:
            GuardrailResult with action=ALLOW.
        """
        return GuardrailResult(action=GuardrailAction.ALLOW)


# ---------------------------------------------------------------------------
# Pre-Tool Hook
# ---------------------------------------------------------------------------


class PreToolHook(ABC):
    """Hook that intercepts before executing a tool.

    Use cases: tool authorization, argument validation, dangerous operation
    blocking.
    """

    @abstractmethod
    async def on_pre_tool_call(
        self,
        name: str,
        arguments: dict,
        context: dict | None,
    ) -> GuardrailResult:
        """Intercept before executing a tool.

        Args:
            name: The registered tool name.
            arguments: The arguments to pass to the tool.
            context: Optional execution context.

        Returns:
            GuardrailResult with allow or block decision.
        """
        ...


class NoOpPreToolHook(PreToolHook):
    """Pass-through pre-tool hook that allows all tool calls."""

    async def on_pre_tool_call(
        self,
        name: str,
        arguments: dict,
        context: dict | None,
    ) -> GuardrailResult:
        """Allow all tool calls without inspection.

        Args:
            name: The registered tool name.
            arguments: The arguments to pass to the tool.
            context: Optional execution context.

        Returns:
            GuardrailResult with action=ALLOW.
        """
        return GuardrailResult(action=GuardrailAction.ALLOW)


# ---------------------------------------------------------------------------
# Post-Tool Hook
# ---------------------------------------------------------------------------


class PostToolHook(ABC):
    """Hook that intercepts after a tool has been executed.

    Use cases: result validation, evidence tracking, output sanitization.
    """

    @abstractmethod
    async def on_post_tool_call(
        self,
        name: str,
        result: Any,
        context: dict | None,
    ) -> GuardrailResult:
        """Intercept after a tool has been executed.

        Args:
            name: The tool that was executed.
            result: The tool's return value.
            context: Optional execution context.

        Returns:
            GuardrailResult with allow or block decision.
        """
        ...


class NoOpPostToolHook(PostToolHook):
    """Pass-through post-tool hook that allows all tool results."""

    async def on_post_tool_call(
        self,
        name: str,
        result: Any,
        context: dict | None,
    ) -> GuardrailResult:
        """Allow all tool results without inspection.

        Args:
            name: The tool that was executed.
            result: The tool's return value.
            context: Optional execution context.

        Returns:
            GuardrailResult with action=ALLOW.
        """
        return GuardrailResult(action=GuardrailAction.ALLOW)
