"""LLM service providing a unified interface to language models.

Wraps LiteLLM for model-agnostic LLM invocations with support for:
- Streaming and non-streaming responses
- Tool calling (function definitions → tool_call → execution → result injection)
- Model fallback strategy
- Intelligent model routing via ModelRouter
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

from hecate_llm.ab_testing import ABTestManager
from hecate_llm.circuit_breaker import CircuitBreakerManager
from hecate_llm.gray_release import GrayReleaseManager
from hecate_llm.routing import ModelRouter, RoutingStrategy

logger = logging.getLogger(__name__)


def _get_litellm() -> Any:
    """Lazy import of litellm to avoid import errors when not installed."""
    try:
        import litellm

        return litellm
    except ImportError as err:
        raise ImportError("litellm is required for LLM service. Install with: pip install hecate[llm]") from err


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
    - Intelligent model routing via ModelRouter
    """

    def __init__(
        self,
        fallback_models: list[str] | None = None,
        router: ModelRouter | None = None,
        circuit_breaker: CircuitBreakerManager | None = None,
        gray_release: GrayReleaseManager | None = None,
        ab_testing: ABTestManager | None = None,
    ):
        self.fallback_models = fallback_models or []
        self.router = router
        # Optional policy layer (phase-4 follow-ups). All default to None =
        # disabled, so existing construction sites and behaviour are
        # unchanged; a composition root wires them in when needed.
        self._breaker = circuit_breaker
        self._gray_release = gray_release
        self._ab_testing = ab_testing

    def _resolve_model(
        self,
        model: str | None = None,
        routing_config: dict[str, Any] | None = None,
    ) -> str:
        """Resolve the model name using routing config or explicit model.

        Priority: explicit ``model`` > gray release (``release_name``) >
        AB test (``test_name``) > router strategy > fallback list.
        Gray release and AB test are mutually exclusive within one
        routing_config (both keys present raises ValueError — a request
        must not be split by two experiments at once).

        Args:
            model: Explicit model name (takes priority).
            routing_config: Optional routing configuration with strategy and constraints.
                May carry ``release_name`` / ``test_name`` / ``context_key``
                for the gray-release and AB-testing policies.

        Returns:
            Resolved model name.
        """
        if model:
            return model

        if routing_config:
            release_name = routing_config.get("release_name")
            test_name = routing_config.get("test_name")
            context_key = routing_config.get("context_key")

            if release_name and test_name:
                msg = "routing_config cannot carry both release_name and test_name"
                raise ValueError(msg)

            if release_name and self._gray_release:
                selected = self._gray_release.select_model(release_name, context_key)
                if selected:
                    return selected
            if test_name and self._ab_testing:
                selected = self._ab_testing.select_model(test_name, context_key)
                if selected:
                    return selected

            if self.router:
                strategy_name = routing_config.get("strategy", "balanced")
                try:
                    strategy = RoutingStrategy(strategy_name)
                except ValueError:
                    strategy = RoutingStrategy.BALANCED

                selected = self.router.select_model(
                    strategy=strategy,
                    required_capabilities=routing_config.get("required_capabilities"),
                    max_cost_per_1k=routing_config.get("max_cost_per_1k"),
                    max_latency_ms=routing_config.get("max_latency_ms"),
                )
                if selected:
                    return selected.name

        # Fallback to first fallback model or default
        if self.fallback_models:
            return self.fallback_models[0]

        return "gpt-4o"

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        routing_config: dict[str, Any] | None = None,
        timeout: float | None = None,
        num_retries: int | None = None,
    ) -> LLMResponse:
        """Invoke a chat completion.

        Args:
            messages: Conversation messages.
            model: Model identifier (e.g., "gpt-4o"). Falls back to routing if None.
            tools: Optional tool definitions for function calling.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens to generate.
            routing_config: Optional routing configuration for model selection.
            timeout: Request timeout in seconds (provider-level override).
            num_retries: Number of retries on failure (provider-level override).

        Returns:
            LLMResponse with content, tool_calls, and usage.
        """
        resolved_model = self._resolve_model(model, routing_config)
        litellm_kwargs: dict[str, Any] = {}
        if timeout is not None:
            litellm_kwargs["timeout"] = timeout
        if num_retries is not None:
            litellm_kwargs["num_retries"] = num_retries

        # Open breaker on the resolved model short-circuits straight to
        # fallback — semantically identical to "first call failed, retry
        # elsewhere" without paying for the doomed request.
        if self._breaker is not None and self._breaker.is_open(resolved_model):
            logger.warning("Circuit open for model %s; falling back", resolved_model)
            if self.fallback_models:
                return await self._try_fallback(messages, tools, temperature, max_tokens, timeout, num_retries)
            msg = f"Circuit open for model {resolved_model} and no fallback models configured"
            raise RuntimeError(msg)

        try:
            response = await _get_litellm().acompletion(
                model=resolved_model,
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                **litellm_kwargs,
            )
            choice = response.choices[0]
            if self._breaker is not None:
                self._breaker.record_success(resolved_model)
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
            logger.warning(f"LLM call failed for model {resolved_model}: {e}")
            if self._breaker is not None:
                self._breaker.record_failure(resolved_model)
            if self.fallback_models:
                return await self._try_fallback(messages, tools, temperature, max_tokens, timeout, num_retries)
            raise

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        routing_config: dict[str, Any] | None = None,
        timeout: float | None = None,
        num_retries: int | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream chat completion chunks.

        Args:
            messages: Conversation messages.
            model: Model identifier. Falls back to routing if None.
            tools: Optional tool definitions.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens to generate.
            routing_config: Optional routing configuration for model selection.
            timeout: Request timeout in seconds (provider-level override).
            num_retries: Number of retries on failure (provider-level override).

        Yields:
            dict with chunk data (content delta, tool_calls, etc.).
        """
        resolved_model = self._resolve_model(model, routing_config)
        litellm_kwargs: dict[str, Any] = {}
        if timeout is not None:
            litellm_kwargs["timeout"] = timeout
        if num_retries is not None:
            litellm_kwargs["num_retries"] = num_retries

        if self._breaker is not None and self._breaker.is_open(resolved_model):
            logger.warning("Circuit open for model %s; falling back (stream)", resolved_model)
            if self.fallback_models:
                async for chunk in self._try_fallback_stream(
                    messages, tools, temperature, max_tokens, timeout, num_retries
                ):
                    yield chunk
                return
            msg = f"Circuit open for model {resolved_model} and no fallback models configured"
            raise RuntimeError(msg)

        try:
            response = await _get_litellm().acompletion(
                model=resolved_model,
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                **litellm_kwargs,
            )
            async for chunk in response:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    yield {
                        "content": delta.content if delta else None,
                        "tool_calls": delta.tool_calls if delta and hasattr(delta, "tool_calls") else None,
                        "finish_reason": chunk.choices[0].finish_reason,
                    }
            if self._breaker is not None:
                self._breaker.record_success(resolved_model)
        except Exception as e:
            logger.warning(f"LLM streaming failed for model {resolved_model}: {e}")
            if self._breaker is not None:
                self._breaker.record_failure(resolved_model)
            if self.fallback_models:
                async for chunk in self._try_fallback_stream(
                    messages,
                    tools,
                    temperature,
                    max_tokens,
                    timeout,
                    num_retries,
                ):
                    yield chunk
            else:
                raise

    async def _try_fallback(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float | None,
        max_tokens: int | None,
        timeout: float | None = None,
        num_retries: int | None = None,
    ) -> LLMResponse:
        """Try fallback models in order."""
        for fallback_model in self.fallback_models:
            try:
                logger.info(f"Trying fallback model: {fallback_model}")
                return await self.chat(
                    messages,
                    fallback_model,
                    tools,
                    temperature,
                    max_tokens,
                    timeout=timeout,
                    num_retries=num_retries,
                )
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
        timeout: float | None = None,
        num_retries: int | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Try fallback models for streaming."""
        for fallback_model in self.fallback_models:
            try:
                logger.info(f"Trying fallback model for streaming: {fallback_model}")
                async for chunk in self.chat_stream(
                    messages,
                    fallback_model,
                    tools,
                    temperature,
                    max_tokens,
                    timeout=timeout,
                    num_retries=num_retries,
                ):
                    yield chunk
                return
            except Exception as e:
                logger.warning(f"Fallback model {fallback_model} also failed: {e}")
                continue
        raise RuntimeError("All models failed")

    def list_models(
        self,
        *,
        provider: str | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> list[str]:
        """Return litellm-recognised model ids for the given provider.

        Used by ``POST /api/model-providers`` (auto-discover) and
        ``GET /api/v1/models`` (fallback when no DB providers exist).
        Returns ``[]`` when litellm is unavailable so callers degrade cleanly
        instead of crashing — the admin UI can fall back to a manual list.
        """
        try:
            litellm = _get_litellm()
        except ImportError:
            logger.warning("litellm not installed; list_models returns []")
            return []
        try:
            return list(
                litellm.get_valid_models(
                    custom_llm_provider=provider,
                    api_key=api_key,
                    api_base=api_base,
                )
                or []
            )
        except Exception as e:
            logger.warning("litellm.get_valid_models failed for provider %r: %s", provider, e)
            return []

    async def test_connection(
        self,
        model_id: str,
        *,
        prompt: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> LLMResponse:
        """Run a one-shot short completion against ``model_id`` to verify reachability.

        Returns a structured result instead of raising so the admin
        ``POST /api/model-providers/{id}/test`` endpoint can render a uniform
        response. On success ``content`` is the model's reply; on failure
        ``content is None`` and ``finish_reason == "error"`` with the
        exception message captured in ``usage``.
        """
        try:
            litellm = _get_litellm()
        except ImportError:
            logger.warning("litellm not installed; test_connection returns error")
            return LLMResponse(model=model_id, finish_reason="error")

        messages = [{"role": "user", "content": prompt}]
        litellm_kwargs: dict[str, Any] = {}
        if api_key is not None:
            litellm_kwargs["api_key"] = api_key
        if api_base is not None:
            litellm_kwargs["api_base"] = api_base
        try:
            response = await litellm.acompletion(
                model=model_id,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **litellm_kwargs,
            )
            choice = response.choices[0]
            return LLMResponse(
                content=choice.message.content,
                model=response.model,
                finish_reason=choice.finish_reason,
            )
        except Exception as e:
            logger.warning("LLM test_connection failed for model %s: %s", model_id, e)
            return LLMResponse(
                model=model_id,
                finish_reason="error",
                usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            )


llm_service = LLMService()
