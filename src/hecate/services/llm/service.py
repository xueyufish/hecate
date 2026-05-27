"""LLM service providing a unified interface to language models.

Wraps LiteLLM for model-agnostic LLM invocations with support for:
- Streaming and non-streaming responses
- Tool calling (function definitions → tool_call → execution → result injection)
- Model fallback strategy
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


def _get_litellm() -> Any:
    """Lazy import of litellm to avoid import errors when not installed."""
    try:
        import litellm

        return litellm
    except ImportError as err:
        raise ImportError(
            "litellm is required for LLM service. Install with: pip install hecate[llm]"
        ) from err


@dataclass
class LLMResponse:
    """Unified LLM response structure."""

    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str | None = None


class LLMService:
    """Service for invoking LLMs via LiteLLM.

    Supports:
    - Multiple model providers (OpenAI, Anthropic, etc.)
    - Streaming and non-streaming responses
    - Tool calling with automatic result injection
    - Model fallback on failure
    """

    def __init__(self, fallback_models: list[str] | None = None):
        self.fallback_models = fallback_models or []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Invoke a chat completion.

        Args:
            messages: Conversation messages.
            model: Model identifier (e.g., "gpt-4o", "claude-3-5-sonnet").
            tools: Optional tool definitions for function calling.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens to generate.

        Returns:
            LLMResponse with content, tool_calls, and usage.
        """
        try:
            response = await _get_litellm().acompletion(
                model=model,
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            choice = response.choices[0]
            return LLMResponse(
                content=choice.message.content,
                tool_calls=[
                    tc.model_dump() if hasattr(tc, "model_dump") else tc for tc in (choice.message.tool_calls or [])
                ],
                model=response.model,
                usage={
                    "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                    "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                    "total_tokens": response.usage.total_tokens if response.usage else 0,
                },
                finish_reason=choice.finish_reason,
            )
        except Exception as e:
            logger.warning(f"LLM call failed for model {model}: {e}")
            if self.fallback_models:
                return await self._try_fallback(messages, tools, temperature, max_tokens)
            raise

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream chat completion chunks.

        Args:
            messages: Conversation messages.
            model: Model identifier.
            tools: Optional tool definitions.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens to generate.

        Yields:
            dict with chunk data (content delta, tool_calls, etc.).
        """
        try:
            response = await _get_litellm().acompletion(
                model=model,
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
            async for chunk in response:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    yield {
                        "content": delta.content if delta else None,
                        "tool_calls": delta.tool_calls if delta and hasattr(delta, "tool_calls") else None,
                        "finish_reason": chunk.choices[0].finish_reason,
                    }
        except Exception as e:
            logger.warning(f"LLM streaming failed for model {model}: {e}")
            if self.fallback_models:
                async for chunk in self._try_fallback_stream(messages, tools, temperature, max_tokens):
                    yield chunk
            else:
                raise

    async def _try_fallback(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> LLMResponse:
        """Try fallback models in order."""
        for fallback_model in self.fallback_models:
            try:
                logger.info(f"Trying fallback model: {fallback_model}")
                return await self.chat(messages, fallback_model, tools, temperature, max_tokens)
            except Exception as e:
                logger.warning(f"Fallback model {fallback_model} also failed: {e}")
                continue
        raise RuntimeError("All models failed")

    async def _try_fallback_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Try fallback models for streaming."""
        for fallback_model in self.fallback_models:
            try:
                logger.info(f"Trying fallback model for streaming: {fallback_model}")
                async for chunk in self.chat_stream(messages, fallback_model, tools, temperature, max_tokens):
                    yield chunk
                return
            except Exception as e:
                logger.warning(f"Fallback model {fallback_model} also failed: {e}")
                continue
        raise RuntimeError("All models failed")


llm_service = LLMService(fallback_models=["gpt-3.5-turbo"])
