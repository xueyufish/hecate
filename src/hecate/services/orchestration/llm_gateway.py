"""LLM gateway resolver — discovers ``hecate.llm_providers`` entry points.

A third-party LLM gateway (OpenRouter, Azure, an in-house router, ...) plugs
in by registering a zero-arg ``provider`` factory under the
``hecate.llm_providers`` entry-point group. The core package selects the
active gateway via ``settings.LLM_PROVIDER`` (env: ``HECATE_LLM_PROVIDER``),
defaulting to ``"litellm"`` — the in-process gateway registered by the
shipped ``hecate-llm`` package.

Selection is single-valued (one active gateway at a time — chat is a global
capability, not a per-request fallback chain). A misconfigured or unknown
name degrades to ``None`` and callers fall back to importing the
``hecate_llm.service.llm_service`` singleton directly, so a missing wheel
never crashes the chat path.

The contract is duck-typed via the ``LLMGateway`` Protocol re-exported from
``hecate_llm.gateway``; no ABC is required on the third-party side.

Today the 15 in-tree consumers import the singleton directly; the resolver
is the seam a future composition root (Phase R) or third-party backend
swaps in. Keeping it alongside ``memory_provider.py`` preserves the
platform's resolver symmetry.
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points

from hecate_llm.gateway import LLMGateway

from hecate.core.config import settings

logger = logging.getLogger(__name__)

_module_cache: LLMGateway | None = None
_resolved: bool = False


def resolve_llm_gateway() -> LLMGateway | None:
    """Return the LLM gateway selected by ``settings.LLM_PROVIDER``.

    Discovers entries in the ``hecate.llm_providers`` group via
    ``importlib.metadata``, picks the one whose ``name`` matches
    ``settings.LLM_PROVIDER`` (default ``"litellm"``), and invokes its
    zero-arg factory. The result is cached module-wide — first call decides
    for the process lifetime; use ``reset_llm_gateway_cache()`` in tests.

    Returns ``None`` if the named entry is missing, the factory raises, or
    the group itself cannot be scanned. Callers fall back to the
    ``hecate_llm.service.llm_service`` singleton when ``None``.
    """
    global _module_cache, _resolved
    if _resolved:
        return _module_cache
    _resolved = True

    name = settings.LLM_PROVIDER
    try:
        eps = entry_points(group="hecate.llm_providers")
    except Exception as e:  # pragma: no cover — defensive, metadata DB corruption
        logger.warning("LLM provider entry-point scan failed: %s", e)
        _module_cache = None
        return None

    for ep in eps:
        if ep.name != name:
            continue
        try:
            _module_cache = ep.load()()
        except Exception:
            logger.exception("LLM provider %r factory raised; falling back to litellm singleton", name)
            _module_cache = None
        break
    else:
        logger.warning(
            "LLM provider %r not found in hecate.llm_providers group (available: %s); "
            "falling back to litellm singleton",
            name,
            [ep.name for ep in eps],
        )
        _module_cache = None

    return _module_cache


def reset_llm_gateway_cache() -> None:
    """Clear the resolver cache. Test-only."""
    global _module_cache, _resolved
    _module_cache = None
    _resolved = False


__all__ = ["LLMGateway", "reset_llm_gateway_cache", "resolve_llm_gateway"]
