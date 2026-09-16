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

from hecate.runtime.context_processors import (
    ChainReport,
    ContextProcessorChain,
    cache_hit_rate_from_usage,
    default_chain_processors,
)
from hecate.runtime.eventstore import Event, EventType
from hecate.runtime.guardrail import (
    GuardrailAction,
    NoOpPostLLMHook,
    NoOpPreLLMHook,
    PostLLMHook,
    PreLLMHook,
)
from hecate.runtime.ports import RuntimePort
from hecate.runtime.task_phase import TaskPhase, detect_task_phase
from hecate.runtime.tool_gate import ToolGateEvaluator
from hecate.runtime.types import WorkerResult
from hecate.runtime.worker import Worker

logger = logging.getLogger(__name__)


def _consume_resume_value(messages: list[dict[str, Any]], channel_snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Inject the human-supplied ``_resume_value`` (HITL correction) into the message stream.

    The Pregel runtime writes the value a human returned during an interrupt
    pause into the ``_resume_value`` channel (see ``PregelRuntime._restore_from_checkpoint``).
    This helper materializes that value as user-role messages so the next LLM
    superstep treats it as a normal conversation turn — that's "result correction":
    a human can rewrite the agent's pending tool call's arguments, supply an
    answer to a clarification prompt, or annotate the agent's intermediate
    output.

    Accepted shapes for ``_resume_value``:
      * ``str`` → wrapped as ``{"role": "user", "content": <str>}``
      * ``{"role": ..., "content": ...}`` → injected as a single message
      * ``{"messages": [...]}`` → the list is appended
      * ``list[...]`` of message dicts → appended verbatim

    The function returns a new messages list (does not mutate the snapshot)
    and signals to the caller that ``_resume_value`` was consumed so the
    channel write can be cleared; otherwise the value would re-inject on
    every subsequent superstep.
    """
    resume_value = channel_snapshot.get("_resume_value")
    if resume_value is None:
        return messages

    new_messages: list[dict[str, Any]] = list(messages)
    if isinstance(resume_value, str):
        new_messages.append({"role": "user", "content": resume_value})
    elif isinstance(resume_value, dict):
        if "messages" in resume_value and isinstance(resume_value["messages"], list):
            new_messages.extend(resume_value["messages"])
        elif "role" in resume_value and "content" in resume_value:
            new_messages.append(resume_value)
        else:
            # Unknown dict shape — keep the agent deterministic by surfacing
            # the correction attempt as a plain user message.
            new_messages.append({"role": "user", "content": str(resume_value)})
    elif isinstance(resume_value, list):
        new_messages.extend(resume_value)
    else:
        new_messages.append({"role": "user", "content": str(resume_value)})
    return new_messages


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


def _hard_truncate_message(msg: dict[str, Any], budget: int, ctx_engine: Any) -> dict[str, Any]:
    """Force a single oversized message under the token budget.

    Retained for direct ``ContextEngine`` consumers of the 4.10 ladder
    helpers; the chain path replaces the emergency ladder with controlled
    termination.
    """
    content = msg.get("content")
    if not isinstance(content, str) or not content:
        return {**msg, "content": "[truncated]"}
    char_limit = max(1, budget * 3)
    truncated = content[:char_limit]
    while truncated and ctx_engine.estimate_tokens([{**msg, "content": truncated}]) > budget:
        truncated = truncated[: len(truncated) // 2]
    return {**msg, "content": truncated + "\n…[emergency truncated]"}


def _emergency_truncate(
    messages: list[dict[str, Any]],
    budget: int,
    ctx_engine: Any,
) -> list[dict[str, Any]]:
    """Final degradation level: hard-truncate the conversation to fit.

    Newest messages are kept first; system messages survive regardless.
    Individually oversized messages get their string content cut down.
    Order of the survivors is preserved.
    """
    if not messages:
        return []

    kept: list[dict[str, Any]] = []
    used = 0
    for msg in reversed(messages):
        if msg.get("role") == "system":
            kept.append(msg)
            continue
        tokens = ctx_engine.estimate_tokens([msg])
        if tokens > budget:
            truncated = _hard_truncate_message(msg, budget - used if budget > used else 1, ctx_engine)
            kept.append(truncated)
            used = budget
            continue
        if used + tokens > budget:
            continue
        kept.append(msg)
        used += tokens
    kept.reverse()
    return kept


async def _run_context_pipeline(
    messages: list[dict[str, Any]],
    node_config: dict[str, Any],
    execution_context: dict | None,
    node_id: str = "",
) -> ChainReport:
    """Project the conversation through the context processor chain (4.13).

    Resolution order:
    - ``execution_context["context_chain"]`` — a ``ContextChainFactory``
      (per-node policy, cached chains) or an already-built chain.
    - Legacy engine-only: a ``ContextEngine`` without a chain runs the
      default chain, which consumes the engine for selection/compression.
    - Neither present: pass-through (no projection).
    """
    if not execution_context:
        return ChainReport(messages=list(messages))
    chain = execution_context.get("context_chain")
    engine = execution_context.get("context_engine")
    if chain is None and engine is None:
        return ChainReport(messages=list(messages))
    if chain is None:
        chain = ContextProcessorChain(default_chain_processors())
    elif not isinstance(chain, ContextProcessorChain):
        chain = chain.chain_for_node(node_config)
    return await chain.apply(messages, node_config, execution_context, node_id)


class LLMWorker(Worker):
    """Worker that executes CONVERSATION-type nodes with full context engineering.

    Internally orchestrates:
    1. PreLLMHook — security check before LLM invocation
    2. Context assembly (via RuntimePort.context_assemble)
    3. Provider-specific shaping
    4. LLM invocation (streaming or non-streaming)
    5. PostLLMHook — output safety check
    6. Evidence tracking (for tool calls)
    7. Channel updates with response and optional ``_has_tool_call`` flag

    Guard hooks are injected at construction time, defaulting to NoOp variants.
    """

    def __init__(
        self,
        port: RuntimePort,
        pre_llm_hook: PreLLMHook | None = None,
        post_llm_hook: PostLLMHook | None = None,
        event_store: Any = None,
        middleware_chains: dict | None = None,
    ) -> None:
        super().__init__(event_store=event_store)
        self._port = port
        self._pre_hook = pre_llm_hook or NoOpPreLLMHook()
        self._post_hook = post_llm_hook or NoOpPostLLMHook()
        # T1.3 (guardrail-upgrade-trio): chain takes precedence when supplied.
        # Legacy single-hook slots remain in service; both may run side-by-side
        # during the migration period. ``middleware_chains`` is the path
        # forward (T3.4 wraps the assembly facade to build it).
        self._middleware_chains = middleware_chains or {}
        self._tool_gate = ToolGateEvaluator()

    def _filter_tools(
        self,
        tools: Any,
        execution_context: dict | None,
        channel_snapshot: dict,
        task_phase: TaskPhase | None = None,
    ) -> Any:
        """Filter tools based on available_when expressions.

        Builds a flat context dict from execution_context and channel_snapshot,
        then delegates to ToolGateEvaluator.filter_tools(). The detected task
        phase (4.9) is published as ``task_phase`` so gate expressions can
        gate tools per conversation phase.

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
        if task_phase is not None:
            context["task_phase"] = task_phase.value

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
        # HITL result-correction: materialise ``_resume_value`` (the value the
        # human returned to the interrupt) as user-role message(s). Cleared
        # after consumption so subsequent turns don't re-inject it.
        consume_resume = "_resume_value" in channel_snapshot
        if consume_resume:
            messages = _consume_resume_value(messages, channel_snapshot)
        model = node_config.get("model", "gpt-4o")
        task_phase = detect_task_phase(messages, channel_snapshot)
        tools = node_config.get("tools")
        tools = self._filter_tools(tools, execution_context, channel_snapshot, task_phase=task_phase)
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
            blocked_updates: dict[str, Any] = {
                "messages": [{"role": "assistant", "content": f"I cannot process this request: {pre_result.reason}"}],
            }
            if consume_resume:
                blocked_updates["_resume_value"] = None
            return WorkerResult(node_id=node_id, channel_updates=blocked_updates)
        if pre_result.action == GuardrailAction.SANITIZE:
            if pre_result.modified_data and "messages" in pre_result.modified_data:
                messages = pre_result.modified_data["messages"]
            else:
                logger.warning(
                    "SANITIZE returned without modified_data on node '%s', treating as ALLOW",
                    node_id,
                )

        # Context pipeline (non-destructive projection via the 4.13 chain)
        chain_report = await _run_context_pipeline(messages, node_config, execution_context, node_id)
        messages = chain_report.messages

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
            "task_phase": task_phase.value,
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

        full_response = ""
        structured_tool_calls: list[dict[str, Any]] | None = None
        provider_usage: dict[str, Any] | None = None
        has_tools = bool(shaped_tools)
        if self._event_store and execution_context:
            from hecate.runtime.eventstore import CURRENT_LOG_SCHEMA_VERSION

            await self._event_store.append(
                Event(
                    session_id=execution_context["session_id"],
                    superstep=execution_context["superstep"],
                    event_type=EventType.LLM_REQUEST,
                    node_id=node_id,
                    trace_id=execution_context.get("trace_id"),
                    payload={
                        "model": model,
                        "messages": shaped_messages,
                        "tools": shaped_tools,
                        "message_count": len(shaped_messages),
                        "task_phase": task_phase.value,
                        "prompt_id": str(prompt_id) if prompt_id is not None else None,
                        "prompt_version": prompt_version,
                        "log_schema_version": CURRENT_LOG_SCHEMA_VERSION,
                    },
                )
            )
        try:
            if has_tools:
                async for chunk in self._port.llm_invoke_structured(
                    messages=shaped_messages,
                    config={"model": model, "tools": shaped_tools},
                ):
                    content = chunk.get("content")
                    chunk_tool_calls = chunk.get("tool_calls")
                    if chunk.get("usage") and isinstance(chunk["usage"], dict):
                        provider_usage = chunk["usage"]
                    if content:
                        if first_token_time is None:
                            first_token_time = time.monotonic()
                        full_response += content
                    if chunk_tool_calls:
                        structured_tool_calls = chunk_tool_calls
            else:
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
            from hecate.runtime.eventstore import CURRENT_LOG_SCHEMA_VERSION

            await self._event_store.append(
                Event(
                    session_id=execution_context["session_id"],
                    superstep=execution_context["superstep"],
                    event_type=EventType.LLM_RESPONSE,
                    node_id=node_id,
                    trace_id=execution_context.get("trace_id"),
                    payload={
                        "model": model,
                        "response_length": len(full_response),
                        "log_schema_version": CURRENT_LOG_SCHEMA_VERSION,
                    },
                )
            )

        llm_end = time.monotonic()
        total_latency_ms = (llm_end - llm_start) * 1000
        ttft_ms = ((first_token_time - llm_start) * 1000) if first_token_time else total_latency_ms

        if span_ctx:
            prompt_tokens = sum(_estimate_message_tokens(m) for m in shaped_messages)
            completion_tokens = len(full_response) // 4
            span_output: dict[str, Any] = {
                "response_length": len(full_response),
                "ttft_ms": ttft_ms,
                "total_latency_ms": total_latency_ms,
            }
            cache_rate = cache_hit_rate_from_usage(provider_usage)
            if cache_rate is not None:
                span_output["cache_hit_rate"] = cache_rate
            await self._port.end_span(
                span_ctx.span_id,
                output_data=span_output,
                usage={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            )

        response_dict: dict[str, Any] = {
            "content": full_response,
            "model": model,
        }
        if structured_tool_calls and chain_report.stop_reason is None:
            # Controlled termination (4.13): pending tool calls are stripped so
            # the agent loop finalizes without new tool invocations.
            response_dict["tool_calls"] = structured_tool_calls
        elif structured_tool_calls:
            logger.info(
                "Token-capped termination on node '%s': suppressing %d pending tool calls",
                node_id,
                len(structured_tool_calls),
            )

        # PostLLMHook
        post_result = await self._post_hook.on_post_llm_call(
            response=response_dict,
            messages=shaped_messages,
        )
        if post_result.action == GuardrailAction.BLOCK:
            logger.info("PostLLMHook blocked response on node '%s': %s", node_id, post_result.reason)
            post_blocked_updates: dict[str, Any] = {
                "messages": [{"role": "assistant", "content": "I cannot provide that response due to safety policy."}],
            }
            if consume_resume:
                post_blocked_updates["_resume_value"] = None
            return WorkerResult(node_id=node_id, channel_updates=post_blocked_updates)
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

        if response_dict.get("tool_calls"):
            assistant_msg["tool_calls"] = response_dict["tool_calls"]
            updates["_has_tool_call"] = True
        else:
            updates["_has_tool_call"] = False

        if consume_resume:
            # Clear the consumed resume value so subsequent turns don't re-inject it.
            updates["_resume_value"] = None

        return WorkerResult(node_id=node_id, channel_updates=updates, stop_reason=chain_report.stop_reason)

    async def execute_stream(
        self,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
        execution_context: dict | None = None,
    ) -> AsyncGenerator[dict[str, Any] | WorkerResult, None]:
        """Execute a streaming LLM call, yielding tokens before final result."""
        messages = channel_snapshot.get("messages", [])
        # HITL result-correction: same as execute() — materialise the
        # human-supplied resume value as user-role messages before the LLM call.
        consume_resume = "_resume_value" in channel_snapshot
        if consume_resume:
            messages = _consume_resume_value(messages, channel_snapshot)
        model = node_config.get("model", "gpt-4o")
        task_phase = detect_task_phase(messages, channel_snapshot)
        tools = node_config.get("tools")
        tools = self._filter_tools(tools, execution_context, channel_snapshot, task_phase=task_phase)
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
            blocked_updates: dict[str, Any] = {
                "messages": [{"role": "assistant", "content": f"I cannot process this request: {pre_result.reason}"}],
            }
            if consume_resume:
                blocked_updates["_resume_value"] = None
            yield WorkerResult(
                node_id=node_id,
                channel_updates=blocked_updates,
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

        # Context pipeline (non-destructive projection via the 4.13 chain)
        chain_report = await _run_context_pipeline(messages, node_config, execution_context, node_id)
        messages = chain_report.messages

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
            attributes={
                "model": model,
                "gen_ai.request.model": model,
                "message_count": len(shaped_messages),
                "task_phase": task_phase.value,
            },
        )

        llm_start = time.monotonic()
        first_token_time: float | None = None

        full_response = ""
        structured_tool_calls: list[dict[str, Any]] | None = None
        provider_usage: dict[str, Any] | None = None
        has_tools = bool(shaped_tools)
        try:
            if has_tools:
                async for chunk in self._port.llm_invoke_structured(
                    messages=shaped_messages,
                    config={"model": model, "tools": shaped_tools},
                ):
                    content = chunk.get("content")
                    chunk_tool_calls = chunk.get("tool_calls")
                    if chunk.get("usage") and isinstance(chunk["usage"], dict):
                        provider_usage = chunk["usage"]
                    if content:
                        if first_token_time is None:
                            first_token_time = time.monotonic()
                        full_response += content
                        yield {"content": content}
                    if chunk_tool_calls:
                        structured_tool_calls = chunk_tool_calls
            else:
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
            prompt_tokens = sum(_estimate_message_tokens(m) for m in shaped_messages)
            completion_tokens = len(full_response) // 4
            span_output: dict[str, Any] = {
                "response_length": len(full_response),
                "ttft_ms": ttft_ms,
                "total_latency_ms": total_latency_ms,
            }
            cache_rate = cache_hit_rate_from_usage(provider_usage)
            if cache_rate is not None:
                span_output["cache_hit_rate"] = cache_rate
            await self._port.end_span(
                span_ctx.span_id,
                output_data=span_output,
                usage={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            )

        response_dict: dict[str, Any] = {
            "content": full_response,
            "model": model,
        }
        if structured_tool_calls and chain_report.stop_reason is None:
            # Controlled termination (4.13): pending tool calls are stripped so
            # the agent loop finalizes without new tool invocations.
            response_dict["tool_calls"] = structured_tool_calls
        elif structured_tool_calls:
            logger.info(
                "Token-capped termination on node '%s': suppressing %d pending tool calls",
                node_id,
                len(structured_tool_calls),
            )

        # PostLLMHook
        post_result = await self._post_hook.on_post_llm_call(
            response=response_dict,
            messages=shaped_messages,
        )
        if post_result.action == GuardrailAction.BLOCK:
            logger.info("PostLLMHook blocked response on node '%s': %s", node_id, post_result.reason)
            stream_post_blocked_updates: dict[str, Any] = {
                "messages": [{"role": "assistant", "content": "I cannot provide that response due to safety policy."}],
            }
            if consume_resume:
                stream_post_blocked_updates["_resume_value"] = None
            yield WorkerResult(node_id=node_id, channel_updates=stream_post_blocked_updates)
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
        else:
            updates["_has_tool_call"] = False

        if consume_resume:
            updates["_resume_value"] = None

        yield WorkerResult(node_id=node_id, channel_updates=updates, stop_reason=chain_report.stop_reason)
