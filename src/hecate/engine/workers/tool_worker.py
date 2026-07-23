"""Tool execution worker with guardrail hook support.

Parses tool calls from the messages channel, invokes PreToolHook before
execution, executes tools via EnginePort, invokes PostToolHook after
execution, captures evidence, and writes tool result messages back to
channel_updates.
"""

from __future__ import annotations

import logging
from typing import Any

from hecate.engine.eventstore import Event, EventType
from hecate.engine.guardrail import (
    GuardrailAction,
    NoOpPostToolHook,
    NoOpPreToolHook,
    PostToolHook,
    PreToolHook,
)
from hecate.engine.ports import EnginePort
from hecate.engine.tool_access import (
    AccessDecision,
    ApprovalCallback,
    ToolAccessPolicy,
    ToolRule,
)
from hecate.engine.tool_matcher import ToolMatcher
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
        access_policy: ToolAccessPolicy | None = None,
        approval_callback: ApprovalCallback | None = None,
        event_store: Any = None,
    ) -> None:
        super().__init__(event_store=event_store)
        self._port = port
        self._pre_hook = pre_tool_hook or NoOpPreToolHook()
        self._post_hook = post_tool_hook or NoOpPostToolHook()
        self._access_policy = access_policy
        self._approval_callback = approval_callback

    async def execute(
        self,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
        execution_context: dict | None = None,
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
            result = await self._execute_single_tool(tc, channel_snapshot, execution_context)
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

    def _check_access(
        self,
        tool_name: str,
        arguments: dict,
        context: dict,
    ) -> AccessDecision | None:
        """Evaluate tool access policy if configured.

        Returns None when no policy is configured (backward compatible),
        allowing all tools to execute as before.
        """
        if self._access_policy is None:
            return None

        tool_meta: dict[str, Any] = {
            "risk_level": context.get("risk_level", "low"),
            "approval_required": context.get("approval_required", False),
            "sandbox_enabled": context.get("sandbox_enabled", False),
            "name": tool_name,
        }
        rules: list[ToolRule] = context.get("tool_rules", [])
        eval_context: dict[str, Any] = {"tool_name": tool_name}
        if "workspace_root" in context:
            eval_context["workspace_root"] = context["workspace_root"]
        return self._access_policy.evaluate(tool_meta, rules, eval_context, arguments=arguments)

    async def _execute_single_tool(
        self,
        tool_call: dict,
        context: dict,
        execution_context: dict | None = None,
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

        access_decision = self._check_access(name, arguments, context)
        if access_decision is not None:
            if access_decision == AccessDecision.DENY:
                return {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": "Tool denied by access policy",
                    "is_error": True,
                }
            if access_decision == AccessDecision.REQUIRE_APPROVAL:
                if self._approval_callback is None:
                    return {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": "Tool requires approval but no callback configured",
                        "is_error": True,
                    }
                approval = await self._approval_callback.request_approval(
                    tool_name=name,
                    arguments=arguments,
                    risk_level=str(context.get("risk_level", "low")),
                    context=context,
                )
                if not approval.approved:
                    return {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": f"Tool call rejected: {approval.reason}",
                        "is_error": True,
                    }

        use_sandbox = access_decision == AccessDecision.EXECUTE_SANDBOX
        if access_decision == AccessDecision.REQUIRE_APPROVAL:
            use_sandbox = context.get("sandbox_enabled", False)

        # Pre-tool hook (with matcher filtering)
        if ToolMatcher.match(name, self._pre_hook.matcher):
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
        span_ctx = await self._port.create_span(
            name=f"tool:{name}",
            attributes={"tool_name": name, "gen_ai.tool.name": name, "arguments": str(arguments)[:500]},
        )
        if self._event_store and execution_context:
            await self._event_store.append(
                Event(
                    session_id=execution_context["session_id"],
                    superstep=execution_context["superstep"],
                    event_type=EventType.TOOL_CALL,
                    node_id=None,
                    payload={"tool_name": name, "arguments": arguments},
                )
            )
        try:
            if use_sandbox:
                from hecate.services.sandbox.environment_bridge import resolve_environment_volumes

                sandbox_context = dict(context) if context else {}
                env = execution_context.get("environment") if execution_context else None
                sandbox_context["_sandbox_volumes"] = resolve_environment_volumes(env)
                result = await self._port.tool_execute_sandbox(
                    name=name,
                    args=arguments,
                    context=sandbox_context,
                )
            else:
                result = await self._port.tool_execute(
                    name=name,
                    args=arguments,
                    context=context,
                )
        except Exception as e:
            logger.warning("Tool '%s' execution failed: %s", name, e)
            if span_ctx:
                await self._port.end_span(span_ctx.span_id, output_data={"error": str(e)})
            return {
                "role": "tool",
                "tool_call_id": tc_id,
                "content": str(e),
                "is_error": True,
            }
        if self._event_store and execution_context:
            await self._event_store.append(
                Event(
                    session_id=execution_context["session_id"],
                    superstep=execution_context["superstep"],
                    event_type=EventType.TOOL_RESULT,
                    node_id=None,
                    payload={"tool_name": name, "result_length": len(str(result))},
                )
            )

        if span_ctx:
            await self._port.end_span(
                span_ctx.span_id,
                output_data={"result_length": len(str(result))},
            )

        # Post-tool hook (with matcher filtering)
        if ToolMatcher.match(name, self._post_hook.matcher):
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
            if post_result.action == GuardrailAction.SANITIZE:
                if post_result.modified_data and "result" in post_result.modified_data:
                    result = post_result.modified_data["result"]
                else:
                    logger.warning("SANITIZE without modified_data for tool '%s'", name)

        return {
            "role": "tool",
            "tool_call_id": tc_id,
            "content": str(result),
        }
