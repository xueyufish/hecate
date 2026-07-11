"""LLM worker for executing CONVERSATION-type nodes.

The most complex production worker, handling the full conversation pre-processing
pipeline: context assembly, memory loading, compression, knowledge retrieval,
provider shaping, LLM invocation, guard hooks, evidence tracking, and optional
token-level streaming.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from hecate.engine.context import ContextEngine
from hecate.engine.eventstore import Event, EventType
from hecate.engine.guardrail import (
    GuardrailAction,
    NoOpPostLLMHook,
    NoOpPreLLMHook,
    PostLLMHook,
    PreLLMHook,
)
from hecate.engine.ports import EnginePort
from hecate.engine.tool_gate import ToolGateEvaluator
from hecate.engine.types import WorkerResult
from hecate.engine.worker import Worker

logger = logging.getLogger(__name__)

_DEFAULT_BUDGET = 8000
_DEFAULT_TOOL_RESULT_LIMIT = 2000
_TRUNCATION_INDICATOR = "\n[... truncated]"


def _estimate_message_tokens(message: dict[str, Any], chars_per_token: int = 4) -> int:
    """Estimate token count for a single message.

    Args:
        message: Message dict with 'content' key.
        chars_per_token: Characters per token for estimation.

    Returns:
        Estimated token count (minimum 1).
    """
    content = message.get("content", "")
    if content is None:
        return 0
    chars = len(content) if isinstance(content, str) else len(str(content))
    return max(1, chars // chars_per_token)


def _truncate_tool_results(
    messages: list[dict[str, Any]],
    tool_result_limit: int,
) -> list[dict[str, Any]]:
    """Truncate oversized tool result content in messages.

    Scans for messages with 'tool_calls' or 'role' == 'tool' whose content
    exceeds the token limit. Returns a new list with truncated copies;
    original messages are not modified.

    Args:
        messages: List of message dicts.
        tool_result_limit: Maximum tokens per tool result.

    Returns:
        New list with truncated tool results where needed.
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role not in ("tool", "assistant"):
            result.append(msg)
            continue

        content = msg.get("content", "")
        if content is None or not isinstance(content, str):
            result.append(msg)
            continue

        estimated = _estimate_message_tokens(msg)
        if estimated <= tool_result_limit:
            result.append(msg)
            continue

        char_limit = tool_result_limit * 4
        truncated_content = content[:char_limit] + _TRUNCATION_INDICATOR
        truncated_msg = {**msg, "content": truncated_content}
        result.append(truncated_msg)

    return result


def _resolve_budget(node_config: dict, execution_context: dict | None) -> int:
    """Resolve token budget with priority: node_config > execution_context > default.

    Args:
        node_config: Per-node configuration dict.
        execution_context: Optional execution context from PregelRuntime.

    Returns:
        Token budget integer.
    """
    node_budget = node_config.get("max_tokens")
    if node_budget is not None and isinstance(node_budget, int) and node_budget > 0:
        return node_budget

    if execution_context:
        ctx_budget = execution_context.get("context_budget")
        if ctx_budget is not None and isinstance(ctx_budget, int) and ctx_budget > 0:
            return ctx_budget

    return _DEFAULT_BUDGET


class LLMWorker(Worker):
    """Worker that executes CONVERSATION-type nodes with full context engineering.

    Internally orchestrates:
    1. PreLLMHook — security check before LLM invocation
    2. Context assembly (via EnginePort.context_assemble)
    3. Provider-specific shaping
    4. LLM invocation (streaming or non-streaming)
    5. PostLLMHook — output safety check
    6. Evidence tracking (for tool calls)
    7. Channel updates with response and optional ``_has_tool_call`` flag

    Guard hooks are injected at construction time, defaulting to NoOp variants.
    """

    def __init__(
        self,
        port: EnginePort,
        pre_llm_hook: PreLLMHook | None = None,
        post_llm_hook: PostLLMHook | None = None,
        event_store: Any = None,
    ) -> None:
        super().__init__(event_store=event_store)
        self._port = port
        self._pre_hook = pre_llm_hook or NoOpPreLLMHook()
        self._post_hook = post_llm_hook or NoOpPostLLMHook()
        self._tool_gate = ToolGateEvaluator()

    @staticmethod
    def _apply_context_pipeline(
        messages: list[dict[str, Any]],
        node_config: dict,
        execution_context: dict | None,
    ) -> list[dict[str, Any]]:
        """Apply context pipeline when ContextEngine is available.

        Non-destructive: returns a new filtered list. Does not modify
        the original messages list or the channel snapshot.

        Steps:
        1. Tool result truncation (cap oversized outputs)
        2. Token estimation against budget
        3. Message selection (if over budget)
        4. Compression (if still over budget)
        """
        ctx_engine: ContextEngine | None = None
        if execution_context:
            ctx_engine = execution_context.get("context_engine")
        if ctx_engine is None:
            return messages

        tool_result_limit = node_config.get("tool_result_limit", _DEFAULT_TOOL_RESULT_LIMIT)
        if not isinstance(tool_result_limit, int) or tool_result_limit <= 0:
            tool_result_limit = _DEFAULT_TOOL_RESULT_LIMIT

        filtered = _truncate_tool_results(messages, tool_result_limit)

        budget = _resolve_budget(node_config, execution_context)
        estimated = ctx_engine.estimate_tokens(filtered)
        if estimated > budget:
            filtered = ctx_engine.select_messages(filtered, budget)
            if ctx_engine.estimate_tokens(filtered) > budget:
                filtered = ctx_engine.compress(filtered)

        return filtered

    def _filter_tools(
        self,
        tools: Any,
        execution_context: dict | None,
        channel_snapshot: dict,
    ) -> Any:
        """Filter tools based on available_when expressions.

        Builds a flat context dict from execution_context and channel_snapshot,
        then delegates to ToolGateEvaluator.filter_tools().

        Returns the original tools list unchanged if tools is not a list.
        """
        if not isinstance(tools, list):
            return tools

        context: dict[str, Any] = {}
        if execution_context:
            context.update(execution_context)
        context.update(channel_snapshot)
        if "_user_id" in context:
            context["user_id"] = context.pop("_user_id")

        return self._tool_gate.filter_tools(tools, context)

    async def execute(
        self,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
        execution_context: dict | None = None,
    ) -> WorkerResult:
        """Execute a non-streaming LLM call with full context engineering."""
        messages = channel_snapshot.get("messages", [])
        model = node_config.get("model", "gpt-4o")
        tools = node_config.get("tools")
        tools = self._filter_tools(tools, execution_context, channel_snapshot)
        session_id = channel_snapshot.get("_session_id")
        agent_id = channel_snapshot.get("_agent_id")

        # PreLLMHook
        pre_result = await self._pre_hook.on_pre_llm_call(
            messages=messages,
            model=model,
            tools=tools,
        )
        if pre_result.action == GuardrailAction.BLOCK:
            logger.info("PreLLMHook blocked LLM call on node '%s': %s", node_id, pre_result.reason)
            return WorkerResult(
                node_id=node_id,
                channel_updates={
                    "messages": [
                        {"role": "assistant", "content": f"I cannot process this request: {pre_result.reason}"}
                    ],
                },
            )
        if pre_result.action == GuardrailAction.SANITIZE:
            if pre_result.modified_data and "messages" in pre_result.modified_data:
                messages = pre_result.modified_data["messages"]
            else:
                logger.warning(
                    "SANITIZE returned without modified_data on node '%s', treating as ALLOW",
                    node_id,
                )

        # Context pipeline (non-destructive message filtering)
        messages = self._apply_context_pipeline(messages, node_config, execution_context)

        # Context assembly
        assembled = await self._port.context_assemble(
            messages=messages,
            tools=tools,
            session_id=session_id or agent_id or "",
            model=model,
        )
        shaped_messages = assembled.get("messages", messages)
        shaped_tools = assembled.get("tools", tools)

        span_attributes: dict[str, Any] = {
            "model": model,
            "gen_ai.request.model": model,
            "message_count": len(shaped_messages),
        }
        prompt_id = node_config.get("prompt_id")
        prompt_version = node_config.get("prompt_version")
        if prompt_id is not None:
            span_attributes["prompt_id"] = str(prompt_id)
        if prompt_version is not None:
            span_attributes["prompt_version"] = prompt_version

        span_ctx = await self._port.create_span(
            name=f"llm:{node_id}",
            attributes=span_attributes,
        )

        llm_start = time.monotonic()
        first_token_time: float | None = None

        # LLM invocation (non-streaming via llm_invoke)
        full_response = ""
        if self._event_store and execution_context:
            await self._event_store.append(
                Event(
                    session_id=execution_context["session_id"],
                    superstep=execution_context["superstep"],
                    event_type=EventType.LLM_REQUEST,
                    node_id=node_id,
                    payload={"model": model, "message_count": len(shaped_messages)},
                )
            )
        try:
            async for token in self._port.llm_invoke(
                messages=shaped_messages,
                config={"model": model, "tools": shaped_tools},
            ):
                if first_token_time is None:
                    first_token_time = time.monotonic()
                full_response += token
        except Exception as e:
            logger.warning("LLM invocation failed for node '%s': %s", node_id, e)
            if span_ctx:
                await self._port.end_span(span_ctx.span_id, output_data={"error": str(e)})
            return WorkerResult(node_id=node_id, error=e)
        if self._event_store and execution_context:
            await self._event_store.append(
                Event(
                    session_id=execution_context["session_id"],
                    superstep=execution_context["superstep"],
                    event_type=EventType.LLM_RESPONSE,
                    node_id=node_id,
                    payload={"model": model, "response_length": len(full_response)},
                )
            )

        llm_end = time.monotonic()
        total_latency_ms = (llm_end - llm_start) * 1000
        ttft_ms = ((first_token_time - llm_start) * 1000) if first_token_time else total_latency_ms

        if span_ctx:
            await self._port.end_span(
                span_ctx.span_id,
                output_data={
                    "response_length": len(full_response),
                    "ttft_ms": ttft_ms,
                    "total_latency_ms": total_latency_ms,
                },
            )

        response_dict: dict[str, Any] = {
            "content": full_response,
            "model": model,
        }

        # PostLLMHook
        post_result = await self._post_hook.on_post_llm_call(
            response=response_dict,
            messages=shaped_messages,
        )
        if post_result.action == GuardrailAction.BLOCK:
            logger.info("PostLLMHook blocked response on node '%s': %s", node_id, post_result.reason)
            return WorkerResult(
                node_id=node_id,
                channel_updates={
                    "messages": [
                        {"role": "assistant", "content": "I cannot provide that response due to safety policy."}
                    ],
                },
            )
        if post_result.action == GuardrailAction.SANITIZE:
            if post_result.modified_data and "response" in post_result.modified_data:
                response_dict = post_result.modified_data["response"]
                full_response = response_dict.get("content", full_response)
            else:
                logger.warning(
                    "SANITIZE returned without modified_data on node '%s', treating as ALLOW",
                    node_id,
                )

        # Build channel updates
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": full_response}
        updates: dict[str, Any] = {"messages": [assistant_msg]}

        # Check for tool calls in response (placeholder — actual tool call
        # detection happens when LLM returns structured responses)
        if response_dict.get("tool_calls"):
            assistant_msg["tool_calls"] = response_dict["tool_calls"]
            updates["_has_tool_call"] = True

        return WorkerResult(node_id=node_id, channel_updates=updates)

    async def execute_stream(
        self,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
        execution_context: dict | None = None,
    ) -> AsyncGenerator[dict[str, Any] | WorkerResult, None]:
        """Execute a streaming LLM call, yielding tokens before final result."""
        messages = channel_snapshot.get("messages", [])
        model = node_config.get("model", "gpt-4o")
        tools = node_config.get("tools")
        tools = self._filter_tools(tools, execution_context, channel_snapshot)
        session_id = channel_snapshot.get("_session_id")
        agent_id = channel_snapshot.get("_agent_id")

        # PreLLMHook
        pre_result = await self._pre_hook.on_pre_llm_call(
            messages=messages,
            model=model,
            tools=tools,
        )
        if pre_result.action == GuardrailAction.BLOCK:
            logger.info("PreLLMHook blocked LLM call on node '%s': %s", node_id, pre_result.reason)
            yield WorkerResult(
                node_id=node_id,
                channel_updates={
                    "messages": [
                        {"role": "assistant", "content": f"I cannot process this request: {pre_result.reason}"}
                    ],
                },
            )
            return
        if pre_result.action == GuardrailAction.SANITIZE:
            if pre_result.modified_data and "messages" in pre_result.modified_data:
                messages = pre_result.modified_data["messages"]
            else:
                logger.warning(
                    "SANITIZE returned without modified_data on node '%s', treating as ALLOW",
                    node_id,
                )

        # Context pipeline (non-destructive message filtering)
        messages = self._apply_context_pipeline(messages, node_config, execution_context)

        # Context assembly
        assembled = await self._port.context_assemble(
            messages=messages,
            tools=tools,
            session_id=session_id or agent_id or "",
            model=model,
        )
        shaped_messages = assembled.get("messages", messages)
        shaped_tools = assembled.get("tools", tools)

        span_ctx = await self._port.create_span(
            name=f"llm_stream:{node_id}",
            attributes={"model": model, "gen_ai.request.model": model, "message_count": len(shaped_messages)},
        )

        llm_start = time.monotonic()
        first_token_time: float | None = None

        # LLM invocation (streaming) — yield tokens as they arrive
        full_response = ""
        try:
            async for token in self._port.llm_invoke(
                messages=shaped_messages,
                config={"model": model, "tools": shaped_tools},
            ):
                if first_token_time is None:
                    first_token_time = time.monotonic()
                full_response += token
                yield {"content": token}
        except Exception as e:
            logger.warning("LLM streaming failed for node '%s': %s", node_id, e)
            if span_ctx:
                await self._port.end_span(span_ctx.span_id, output_data={"error": str(e)})
            yield WorkerResult(node_id=node_id, error=e)
            return

        llm_end = time.monotonic()
        total_latency_ms = (llm_end - llm_start) * 1000
        ttft_ms = ((first_token_time - llm_start) * 1000) if first_token_time else total_latency_ms

        if span_ctx:
            await self._port.end_span(
                span_ctx.span_id,
                output_data={
                    "response_length": len(full_response),
                    "ttft_ms": ttft_ms,
                    "total_latency_ms": total_latency_ms,
                },
            )

        response_dict: dict[str, Any] = {
            "content": full_response,
            "model": model,
        }

        # PostLLMHook
        post_result = await self._post_hook.on_post_llm_call(
            response=response_dict,
            messages=shaped_messages,
        )
        if post_result.action == GuardrailAction.BLOCK:
            logger.info("PostLLMHook blocked response on node '%s': %s", node_id, post_result.reason)
            yield WorkerResult(
                node_id=node_id,
                channel_updates={
                    "messages": [
                        {"role": "assistant", "content": "I cannot provide that response due to safety policy."}
                    ],
                },
            )
            return
        if post_result.action == GuardrailAction.SANITIZE:
            if post_result.modified_data and "response" in post_result.modified_data:
                response_dict = post_result.modified_data["response"]
                full_response = response_dict.get("content", full_response)
            else:
                logger.warning(
                    "SANITIZE returned without modified_data on node '%s', treating as ALLOW",
                    node_id,
                )

        # Build final WorkerResult
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": full_response}
        updates: dict[str, Any] = {"messages": [assistant_msg]}

        if response_dict.get("tool_calls"):
            assistant_msg["tool_calls"] = response_dict["tool_calls"]
            updates["_has_tool_call"] = True

        yield WorkerResult(node_id=node_id, channel_updates=updates)
