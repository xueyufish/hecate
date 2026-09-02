"""HookStageAdapter — bridge legacy guardrail hook ABCs onto the chain kernel.

The four legacy hook ABCs (``PreLLMHook`` / ``PostLLMHook`` / ``PreToolHook``
/ ``PostToolHook``) remain as backward-compatible single-stage adapters. Each
adapter wraps an existing hook implementation into a stage that participates
in the chain kernel's order / short-circuit / monotonicity semantics without
forcing the underlying implementation to learn the ``CallNextOutcome``
protocol.

The adapter preserves the legacy ``matcher`` tool-name filter (no match →
no-op pass-through). SANITIZE results lacking ``modified_data`` are upgraded
to BLOCK by the chain kernel itself, so the adapter simply forwards the
result.
"""

from __future__ import annotations

from hecate.runtime.guardrail import (
    PostLLMHook,
    PostToolHook,
    PreLLMHook,
    PreToolHook,
)
from hecate.runtime.middleware import (
    StageDecision,
    StageHandler,
)
from hecate.runtime.tool_matcher import ToolMatcher


def _to_stage_decision(stage_id: str, result) -> StageDecision:
    """Translate a legacy ``GuardrailResult`` into a ``StageDecision``."""
    return StageDecision(
        stage_id=stage_id,
        action=result.action,
        reason=result.reason,
        modified_data=result.modified_data,
    )


def adapt_pre_llm_hook(stage_id: str, hook: PreLLMHook) -> StageHandler:
    """Wrap a ``PreLLMHook`` implementation as a stage handler (factory).

    Returns a stage handler (not a stage) — the chain kernel adds it under
    ``stage_id``. The handler performs the legacy pre-LLM call, then forwards
    the (possibly-sanitized) messages to subsequent stages.
    """

    async def handler(data, call_next):
        # data shape: {"messages": [...], "model": ..., "tools": ...}
        messages = data.get("messages") if isinstance(data, dict) else data
        model = data.get("model", "") if isinstance(data, dict) else ""
        tools = data.get("tools") if isinstance(data, dict) else None
        result = await hook.on_pre_llm_call(messages, model, tools)
        decision = _to_stage_decision(stage_id, result)
        if decision.action.value == "allow":
            outcome = await call_next(data)
            if outcome.blocked:
                return outcome.decision, outcome
            return decision, outcome.result
        # BLOCK / SANITIZE without invoking next — the kernel applies the
        # SANITIZE contract check on its own.
        return decision, None

    return handler


def adapt_post_llm_hook(stage_id: str, hook: PostLLMHook) -> StageHandler:
    """Wrap a ``PostLLMHook`` implementation as a stage handler."""

    async def handler(data, call_next):
        response = data.get("response") if isinstance(data, dict) else data
        messages = data.get("messages", []) if isinstance(data, dict) else []
        result = await hook.on_post_llm_call(response, messages)
        decision = _to_stage_decision(stage_id, result)
        if decision.action.value == "allow":
            outcome = await call_next(data)
            if outcome.blocked:
                return outcome.decision, outcome
            return decision, outcome.result
        return decision, None

    return handler


def adapt_pre_tool_hook(stage_id: str, hook: PreToolHook) -> StageHandler:
    """Wrap a ``PreToolHook`` implementation as a stage handler.

    Honors the legacy ``matcher`` filter: when the hook's matcher rejects
    the tool name, the adapter acts as a passthrough (no-op ALLOW).
    """

    async def handler(data, call_next):
        tool_name = data.get("name", "") if isinstance(data, dict) else ""
        if not ToolMatcher.match(tool_name, hook.matcher):
            outcome = await call_next(data)
            if outcome.blocked:
                return outcome.decision, outcome
            return StageDecision.allow(stage_id=stage_id), outcome.result
        arguments = data.get("arguments", {}) if isinstance(data, dict) else {}
        context = data.get("context") if isinstance(data, dict) else None
        result = await hook.on_pre_tool_call(tool_name, arguments, context)
        decision = _to_stage_decision(stage_id, result)
        if decision.action.value == "allow":
            outcome = await call_next(data)
            if outcome.blocked:
                return outcome.decision, outcome
            return decision, outcome.result
        return decision, None

    return handler


def adapt_post_tool_hook(stage_id: str, hook: PostToolHook) -> StageHandler:
    """Wrap a ``PostToolHook`` implementation as a stage handler."""

    async def handler(data, call_next):
        tool_name = data.get("name", "") if isinstance(data, dict) else ""
        result_value = data.get("result") if isinstance(data, dict) else None
        context = data.get("context") if isinstance(data, dict) else None
        if not ToolMatcher.match(tool_name, hook.matcher):
            outcome = await call_next(data)
            if outcome.blocked:
                return outcome.decision, outcome
            return StageDecision.allow(stage_id=stage_id), outcome.result
        result = await hook.on_post_tool_call(tool_name, result_value, context)
        decision = _to_stage_decision(stage_id, result)
        if decision.action.value == "allow":
            outcome = await call_next(data)
            if outcome.blocked:
                return outcome.decision, outcome
            return decision, outcome.result
        return decision, None

    return handler


__all__ = [
    "adapt_pre_llm_hook",
    "adapt_post_llm_hook",
    "adapt_pre_tool_hook",
    "adapt_post_tool_hook",
]
