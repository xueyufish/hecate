"""Chain assembly helpers — wrap legacy single-hook wiring into chain dicts.

The middleware-chain refactor replaces each single-hook slot with a dict of
ordered chains (``{Phase: Chain}``). Legacy single-hook construction
parameters remain supported: a single hook is wrapped into a chain with one
stage via the stage adapter, so existing callers see no behavioral change.

The factory lives in the runtime layer so it does not depend on services.
"""

from __future__ import annotations

from hecate.runtime.guardrail import (
    GuardrailResult,
    PostLLMHook,
    PostToolHook,
    PreLLMHook,
    PreToolHook,
)
from hecate.runtime.middleware import Chain, Phase
from hecate.runtime.middleware_adapters import (
    adapt_post_llm_hook,
    adapt_post_tool_hook,
    adapt_pre_llm_hook,
    adapt_pre_tool_hook,
)


def build_llm_chain(
    pre_hook: PreLLMHook | None,
    post_hook: PostLLMHook | None,
) -> dict[Phase, Chain]:
    """Build the LLM-side chain dict (pre-request + post-response).

    Each chain has one stage (the wrapped hook) and no terminal handler — the
    worker supplies the terminal handler when running the chain. A ``None``
    hook is treated as a passthrough NoOp at the adapter level.
    """
    pre_chain = Chain(phase=Phase.AGENT_REQUEST)
    pre_chain.add_stage("pre-llm", adapt_pre_llm_hook("pre-llm", pre_hook or _NoOpPreLLM()))

    post_chain = Chain(phase=Phase.LLM_RESPONSE)
    post_chain.add_stage("post-llm", adapt_post_llm_hook("post-llm", post_hook or _NoOpPostLLM()))

    return {Phase.AGENT_REQUEST: pre_chain, Phase.LLM_RESPONSE: post_chain}


def build_tool_chain(
    pre_hook: PreToolHook | None,
    post_hook: PostToolHook | None,
) -> dict[Phase, Chain]:
    """Build the tool-side chain dict (pre-execute + post-result)."""
    pre_chain = Chain(phase=Phase.TOOL_PRE_EXECUTE)
    pre_chain.add_stage("pre-tool", adapt_pre_tool_hook("pre-tool", pre_hook or _NoOpPreTool()))

    post_chain = Chain(phase=Phase.TOOL_RESULT)
    post_chain.add_stage("post-tool", adapt_post_tool_hook("post-tool", post_hook or _NoOpPostTool()))

    return {Phase.TOOL_PRE_EXECUTE: pre_chain, Phase.TOOL_RESULT: post_chain}


class _NoOpPreLLM(PreLLMHook):
    async def on_pre_llm_call(self, messages, model, tools):
        return GuardrailResult()


class _NoOpPostLLM(PostLLMHook):
    async def on_post_llm_call(self, response, messages):
        return GuardrailResult()


class _NoOpPreTool(PreToolHook):
    matcher = None

    async def on_pre_tool_call(self, name, arguments, context):
        return GuardrailResult()


class _NoOpPostTool(PostToolHook):
    matcher = None

    async def on_post_tool_call(self, name, result, context):
        return GuardrailResult()


__all__ = ["build_llm_chain", "build_tool_chain"]
