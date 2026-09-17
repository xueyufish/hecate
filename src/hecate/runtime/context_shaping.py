"""Provider-shaped context strategies (Context Engineering).

Different LLM providers reject different conversation shapes. This module
provides per-provider message-shaping strategies selected automatically from
the model name, so ``RuntimePort.context_assemble`` (4.11) can hand each
provider a conversation it will accept:

- ``AnthropicShapingStrategy`` — consolidates scattered system messages into
  a single system message and drops empty-content messages (Anthropic
  rejects empty text blocks and expects one top-level system block).
- ``OpenAIShapingStrategy`` — drops orphan ``tool`` messages whose
  ``tool_call_id`` is not introduced by the preceding assistant message
  (OpenAI rejects tool messages that don't answer a ``tool_calls`` block)
  and merges consecutive system messages.
- ``DefaultShapingStrategy`` — conservative baseline: drops empty-content
  messages only.

Strategy resolution is prefix-based: models starting with ``claude`` get the
Anthropic strategy; everything else (gpt/o*/gemini/qwen/deepseek/glm/…)
routes through the OpenAI-compatible default.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ProviderShapingStrategy(ABC):
    """Strategy interface for provider-specific conversation shaping."""

    @abstractmethod
    def shape_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return a provider-acceptable copy of the conversation.

        Implementations must be non-destructive: they return a new list and
        never mutate the input messages.
        """
        ...

    def shape_tools(self, tools: list[Any] | None) -> list[Any] | None:
        """Return a provider-acceptable copy of the tool definitions.

        Default: pass through unchanged (tool schemas are already emitted in
        OpenAI function-call format by the tool registry; LiteLLM performs
        provider translation downstream).
        """
        return tools


def _drop_empty_content(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop non-tool messages whose content is empty/whitespace.

    ``tool``-role messages carry results via content and must survive with
    whatever they have; assistant messages carrying ``tool_calls`` are the
    pairing anchors for those results and survive regardless of their
    (usually empty) text content.
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if msg.get("tool_calls"):
            result.append(msg)
            continue
        if role in ("system", "user", "assistant") and (
            content is None or (isinstance(content, str) and not content.strip())
        ):
            continue
        result.append(msg)
    return result


def _consolidate_system(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge all system messages into the first one, preserving order."""
    system_texts: list[str] = []
    for msg in messages:
        if msg.get("role") == "system" and isinstance(msg.get("content"), str):
            system_texts.append(msg["content"])

    if len(system_texts) <= 1:
        return messages

    merged = "\n\n".join(system_texts)
    result: list[dict[str, Any]] = []
    system_seen = False
    for msg in messages:
        if msg.get("role") == "system":
            if not system_seen:
                result.append({**msg, "content": merged})
                system_seen = True
            continue
        result.append(msg)
    return result


def _drop_orphan_tool_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop tool messages with no immediately-pending assistant tool_call.

    OpenAI requires every ``tool`` message to answer the most recent
    assistant ``tool_calls`` block; any intervening non-tool message (e.g.
    a user turn) invalidates the pending window, so later tool results
    become orphans and are dropped rather than triggering a 400.
    """
    pending_ids: set[str] = set()
    result: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role == "assistant":
            tool_calls = msg.get("tool_calls") or []
            pending_ids = {tc.get("id") for tc in tool_calls if isinstance(tc, dict) and tc.get("id")}
            result.append(msg)
        elif role == "tool":
            call_id = msg.get("tool_call_id")
            if call_id is not None and call_id in pending_ids:
                result.append(msg)
                pending_ids.discard(call_id)
            # Orphan tool message (no pending window): silently dropped.
        else:
            result.append(msg)
            pending_ids = set()
    return result


def _strip_cache_hints(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove runtime-internal annotation keys (``cache_hint``, ``citations``).

    These keys are runtime→shaping contracts (chain annotations, 1.3.5e
    citation metadata), never wire fields — providers must not see them.
    """
    return [{k: v for k, v in msg.items() if k not in ("cache_hint", "citations")} for msg in messages]


def _render_cache_hints(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Render ``cache_hint`` annotations into Anthropic ``cache_control`` blocks.

    A message annotated by the KVCacheAwareProcessor (4.13) gets its string
    content converted to a single text block carrying
    ``cache_control: {"type": "ephemeral"}`` — the Anthropic cache breakpoint
    syntax. The annotation keys themselves are always stripped.
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        hint = msg.get("cache_hint")
        cleaned = {k: v for k, v in msg.items() if k not in ("cache_hint", "citations")}
        if hint == "breakpoint" and msg.get("role") in ("user", "assistant") and isinstance(msg.get("content"), str):
            cleaned["content"] = [
                {"type": "text", "text": msg["content"], "cache_control": {"type": "ephemeral"}},
            ]
        result.append(cleaned)
    return result


class AnthropicShapingStrategy(ProviderShapingStrategy):
    """Shaping for Anthropic Claude models (single system block, no empties)."""

    def shape_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _drop_empty_content(_consolidate_system(_render_cache_hints(list(messages))))


class OpenAIShapingStrategy(ProviderShapingStrategy):
    """Shaping for OpenAI-compatible models (no orphan tool messages)."""

    def shape_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _drop_orphan_tool_messages(_strip_cache_hints(list(messages)))


class DefaultShapingStrategy(ProviderShapingStrategy):
    """Conservative baseline shaping for unrecognized providers."""

    def shape_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _drop_empty_content(_strip_cache_hints(list(messages)))


_ANTHROPIC_PREFIXES = ("claude",)


def resolve_shaping_strategy(model: str) -> ProviderShapingStrategy:
    """Resolve the shaping strategy from a model name (prefix detection).

    Args:
        model: Model identifier, e.g. ``claude-sonnet-4`` or ``gpt-4o``.
            Prefixed routing forms like ``anthropic/claude-3`` and
            ``openai/gpt-4o`` are unwrapped before matching.

    Returns:
        AnthropicShapingStrategy for ``claude`` models (with or without a
        provider prefix), otherwise the OpenAI-compatible default.
    """
    candidate = model.rsplit("/", 1)[-1].strip().lower()
    if candidate.startswith(_ANTHROPIC_PREFIXES):
        return AnthropicShapingStrategy()
    return OpenAIShapingStrategy()


def shape_context(
    messages: list[dict[str, Any]],
    tools: list[Any] | None,
    model: str,
) -> tuple[list[dict[str, Any]], list[Any] | None, str]:
    """Shape a conversation for its target provider.

    Convenience entry point used by RuntimePort adapters: resolves the
    strategy for *model* and applies it to messages and tools.

    Returns:
        Tuple of (shaped_messages, shaped_tools, strategy_name).
    """
    strategy = resolve_shaping_strategy(model)
    return strategy.shape_messages(messages), strategy.shape_tools(tools), type(strategy).__name__
