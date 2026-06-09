"""Security hooks for agent execution safety."""

from __future__ import annotations

from typing import NamedTuple

from hecate.engine.guardrail import (
    NoOpPostLLMHook,
    NoOpPostToolHook,
    NoOpPreLLMHook,
    NoOpPreToolHook,
    PostLLMHook,
    PostToolHook,
    PreLLMHook,
    PreToolHook,
)
from hecate.services.security.hooks.input_security import InputSecurityHook
from hecate.services.security.hooks.output_security import OutputSecurityHook
from hecate.services.security.hooks.tool_result_security import ToolResultSecurityHook


class SecurityHookSet(NamedTuple):
    """Bundle of four security hooks for agent execution."""

    pre_llm_hook: PreLLMHook
    post_llm_hook: PostLLMHook
    pre_tool_hook: PreToolHook
    post_tool_hook: PostToolHook


def create_security_hooks(guardrail_config: dict | None = None) -> SecurityHookSet:
    """Construct a SecurityHookSet from per-agent guardrail configuration.

    Args:
        guardrail_config: Agent-specific guardrail config dict with optional
            sections: ``input_security``, ``output_security``, ``data_security``.
            When ``None`` or empty, returns NoOp hooks for all four positions.

    Returns:
        SecurityHookSet with configured or NoOp hooks.
    """
    if not guardrail_config:
        return SecurityHookSet(
            pre_llm_hook=NoOpPreLLMHook(),
            post_llm_hook=NoOpPostLLMHook(),
            pre_tool_hook=NoOpPreToolHook(),
            post_tool_hook=NoOpPostToolHook(),
        )

    input_cfg = guardrail_config.get("input_security")
    output_cfg = guardrail_config.get("output_security")
    data_cfg = guardrail_config.get("data_security")

    pre_llm = (
        InputSecurityHook(
            enabled=input_cfg.get("enabled", True),
            pii_entities=input_cfg.get("pii_entities"),
            block_on_injection=input_cfg.get("block_on_injection", True),
        )
        if input_cfg and input_cfg.get("enabled", True)
        else NoOpPreLLMHook()
    )

    post_llm = (
        OutputSecurityHook(
            enabled=output_cfg.get("enabled", True),
            toxicity_threshold=output_cfg.get("toxicity_threshold", 0.7),
            deanonymize=output_cfg.get("deanonymize", True),
        )
        if output_cfg and output_cfg.get("enabled", True)
        else NoOpPostLLMHook()
    )

    pre_tool = NoOpPreToolHook()

    post_tool = (
        ToolResultSecurityHook(
            mask_tool_results=data_cfg.get("mask_tool_results", True),
        )
        if data_cfg and data_cfg.get("enabled", True)
        else NoOpPostToolHook()
    )

    return SecurityHookSet(
        pre_llm_hook=pre_llm,
        post_llm_hook=post_llm,
        pre_tool_hook=pre_tool,
        post_tool_hook=post_tool,
    )
