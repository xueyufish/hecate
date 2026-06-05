"""Tool execution worker with guardrail hook support.

Parses tool calls from the messages channel, invokes PreToolHook before
execution, executes tools via EnginePort, invokes PostToolHook after
execution, captures evidence, and writes tool result messages back to
channel_updates.
"""

from __future__ import annotations

import logging
from typing import Any

from hecate.engine.guardrail import (
    GuardrailAction,
    NoOpPostToolHook,
    NoOpPreToolHook,
    PostToolHook,
    PreToolHook,
)
from hecate.engine.ports import EnginePort
from hecate.engine.types import WorkerResult
from hecate.engine.worker import Worker

logger = logging.getLogger(__name__)


class ToolWorker(Worker):
    """Worker that executes tool calls from the messages channel.

    Extracts tool calls from the last assistant message, executes each tool
    via EnginePort, captures evidence, and returns tool result messages.

    Guard hooks are called before and after each tool execution:
    - ``PreToolHook``: called before execution; on BLOCK, the tool is skipped.
    - ``PostToolHook``: called after execution; on BLOCK, the result is sanitized.
    """

    def __init__(
        self,
        port: EnginePort,
        pre_tool_hook: PreToolHook | None = None,
        post_tool_hook: PostToolHook | None = None,
    ) -> None:
        self._port = port
        self._pre_hook = pre_tool_hook or NoOpPreToolHook()
        self._post_hook = post_tool_hook or NoOpPostToolHook()

    async def execute(
        self,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
    ) -> WorkerResult:
        messages = channel_snapshot.get("messages", [])
        tool_calls = self._extract_tool_calls(messages)

        if not tool_calls:
            return WorkerResult(
                node_id=node_id,
                channel_updates={"messages": []},
            )

        tool_results: list[dict[str, Any]] = []
        for tc in tool_calls:
            result = await self._execute_single_tool(tc, channel_snapshot)
            tool_results.append(result)

        return WorkerResult(
            node_id=node_id,
            channel_updates={"messages": tool_results},
        )

    def _extract_tool_calls(self, messages: list[dict]) -> list[dict]:
        """Extract tool calls from the last assistant message.

        Args:
            messages: Channel messages list.

        Returns:
            List of tool call dicts with id, name, arguments.
        """
        for msg in reversed(messages):
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                return msg["tool_calls"]
        return []

    async def _execute_single_tool(
        self,
        tool_call: dict,
        context: dict,
    ) -> dict[str, Any]:
        """Execute a single tool call with pre/post hooks.

        Args:
            tool_call: Dict with id, function/name, function/arguments.
            context: Channel snapshot for hook context.

        Returns:
            Tool result message dict.
        """
        tc_id = tool_call.get("id", "")
        func_info = tool_call.get("function", {})
        name = func_info.get("name", tool_call.get("name", "unknown"))
        arguments = func_info.get("arguments", tool_call.get("arguments", {}))

        if isinstance(arguments, str):
            import json

            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}

        # Pre-tool hook
        pre_result = await self._pre_hook.on_pre_tool_call(
            name=name,
            arguments=arguments,
            context=context,
        )
        if pre_result.action == GuardrailAction.BLOCK:
            logger.info("PreToolHook blocked tool '%s': %s", name, pre_result.reason)
            return {
                "role": "tool",
                "tool_call_id": tc_id,
                "content": f"Tool blocked: {pre_result.reason}",
                "is_error": True,
            }

        # Execute tool
        try:
            result = await self._port.tool_execute(
                name=name,
                args=arguments,
                context=context,
            )
        except Exception as e:
            logger.warning("Tool '%s' execution failed: %s", name, e)
            return {
                "role": "tool",
                "tool_call_id": tc_id,
                "content": str(e),
                "is_error": True,
            }

        # Post-tool hook
        post_result = await self._post_hook.on_post_tool_call(
            name=name,
            result=result,
            context=context,
        )
        if post_result.action == GuardrailAction.BLOCK:
            logger.info("PostToolHook sanitized tool '%s': %s", name, post_result.reason)
            return {
                "role": "tool",
                "tool_call_id": tc_id,
                "content": f"Result sanitized: {post_result.reason}",
            }

        return {
            "role": "tool",
            "tool_call_id": tc_id,
            "content": str(result),
        }
