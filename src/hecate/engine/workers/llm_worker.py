"""LLM worker for executing CONVERSATION-type nodes.

The most complex production worker, handling the full conversation pre-processing
pipeline: context assembly, memory loading, compression, knowledge retrieval,
provider shaping, LLM invocation, guard hooks, evidence tracking, and optional
token-level streaming.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from hecate.engine.eventstore import Event, EventType
from hecate.engine.guardrail import (
    GuardrailAction,
    NoOpPostLLMHook,
    NoOpPreLLMHook,
    PostLLMHook,
    PreLLMHook,
)
from hecate.engine.ports import EnginePort
from hecate.engine.types import WorkerResult
from hecate.engine.worker import Worker

logger = logging.getLogger(__name__)


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
            name=f"llm:{node_id}",
            attributes={"model": model, "message_count": len(shaped_messages)},
        )

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

        if span_ctx:
            await self._port.end_span(
                span_ctx.span_id,
                output_data={"response_length": len(full_response)},
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
            attributes={"model": model, "message_count": len(shaped_messages)},
        )

        # LLM invocation (streaming) — yield tokens as they arrive
        full_response = ""
        try:
            async for token in self._port.llm_invoke(
                messages=shaped_messages,
                config={"model": model, "tools": shaped_tools},
            ):
                full_response += token
                yield {"content": token}
        except Exception as e:
            logger.warning("LLM streaming failed for node '%s': %s", node_id, e)
            if span_ctx:
                await self._port.end_span(span_ctx.span_id, output_data={"error": str(e)})
            yield WorkerResult(node_id=node_id, error=e)
            return

        if span_ctx:
            await self._port.end_span(
                span_ctx.span_id,
                output_data={"response_length": len(full_response)},
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
