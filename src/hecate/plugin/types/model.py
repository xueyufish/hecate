"""ModelPluginABC — base class for model-type plugins (custom LLM provider)."""

from __future__ import annotations

from typing import Any


class ModelPluginABC:
    """Base class for plugins that provide custom LLM inference.

    Use this when LiteLLM does not cover a provider and you need
    a custom inference backend (e.g., vLLM, TGI, internal model server).
    """

    async def invoke(self, messages: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    async def embed(self, text: str) -> list[float]:
        raise NotImplementedError
