"""LLMGateway protocol — the single front door for every LLM call.

PR4a convergence: every consumer of language models (chat path, MCP server,
ops-center evaluators, management model-testing endpoint, ...) talks to this
contract. The default implementation lives in ``service.py`` (LLMService);
real provider-specific backends (OpenRouter, Azure, ...) plug in as
additional entry points under ``hecate.llm_providers`` once PR4b ships.

Why a Protocol and not a BaseClass:
- matches the duck-typed consumer surface already in use (chat/chat_stream
  attributes are read from the ``llm_service`` singleton today)
- no inheritance tax for test doubles
- same shape as ``MemoryProvider`` (PR2.2) — consistent gateway style across
  the platform

Embedding is explicitly OUT OF SCOPE: vector embeddings for hybrid search
go through ``core.composition.memory_provider``, not through the LLM
gateway. This matches today's reality — ``grep -r 'litellm.embedding' src/``
returns zero hits.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

from hecate_llm.service import LLMResponse


class LLMGateway(Protocol):
    """The single entry point for every LLM call in the platform.

    Implementations MUST funnel every provider-specific SDK call (litellm,
    OpenAI SDK, ...) through this surface so that no other module imports a
    provider library directly.
    """

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        routing_config: dict[str, Any] | None = None,
        timeout: float | None = None,
        num_retries: int | None = None,
    ) -> LLMResponse:
        """Single-shot chat completion. Returns full LLMResponse."""
        ...

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        routing_config: dict[str, Any] | None = None,
        timeout: float | None = None,
        num_retries: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Streaming chat completion. Yields per-chunk dicts."""
        ...

    def list_models(
        self,
        *,
        provider: str | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> list[str]:
        """Return the model ids litellm recognises for the given provider.

        Used by ``POST /api/model-providers`` to auto-discover a provider's
        catalogue and by ``GET /api/v1/models`` as the fallback when no
        DB-registered providers exist.
        """
        ...

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

        Returns a sentinel ``LLMResponse(content="...")`` on success or
        ``LLMResponse(content=None, finish_reason="error")`` on failure —
        the caller inspects ``finish_reason`` (or the absence of ``content``).
        Never raises; surfaces vendor errors as a structured result so the
        admin endpoint can render a uniform 200.
        """
        ...


__all__ = ["LLMGateway"]
