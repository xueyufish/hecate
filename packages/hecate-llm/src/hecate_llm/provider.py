"""LLM provider entry-point factory.

Registered under the ``hecate.llm_providers`` group as ``litellm``. The core
package discovers this entry point via ``importlib.metadata`` and, when
``HECATE_LLM_PROVIDER`` selects it (the default), uses the returned object
as the active LLM gateway.

Third-party LLM gateway packages (e.g. a hypothetical ``hecate-llm-openrouter``)
should declare their own entry under the same group with a distinct name and
implement the ``LLMGateway`` Protocol from ``hecate_llm.gateway`` — the same
duck-typed contract, no inheritance required.
"""

from __future__ import annotations

from .service import LLMService, llm_service


def provider() -> LLMService:
    """Zero-arg factory returning the default litellm-backed gateway singleton."""
    return llm_service


__all__ = ["provider"]
