"""Tool execution worker with guardrail hook support.

Parses tool calls from the messages channel, invokes PreToolHook before
execution, executes tools via RuntimePort, invokes PostToolHook after
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
from hecate.engine.ports import RuntimePort
from hecate.engine.tool_access import (
    AccessDecision,
    ApprovalCallback,
    ToolAccessPolicy,
    ToolRule,
)
from hecate.engine.tool_matcher import ToolMatcher
from hecate.engine.types import WorkerResult
from hecate.engine.worker import Worker
from hecate.engine.workers.sandbox_router import SandboxEnforcementRouter

logger = logging.getLogger(__name__)


class ToolWorker(Worker):
    """Worker that executes tool calls from the messages channel.

    Extracts tool calls from the last assistant message, executes each tool
    via RuntimePort, captures evidence, and returns tool result messages.

    Guard hooks are called before and after each tool execution:
    - ``PreToolHook``: called before execution; on BLOCK, the tool is skipped.
    - ``PostToolHook``: called after execution; on BLOCK, the result is sanitized.
    """

    def __init__(
        self,
        port: RuntimePort,
        pre_tool_hook: PreToolHook | None = None,
        post_tool_hook: PostToolHook | None = None,
        access_policy: ToolAccessPolicy | None = None,
        approval_callback: ApprovalCallback | None = None,
        event_store: Any = None,
        sandbox_enforcement: SandboxEnforcementRouter | None = None,
        tool_rules: list[ToolRule] | None = None,
        middleware_chains: dict | None = None,
        denial_tracker: Any | None = None,
    ) -> None:
        super().__init__(event_store=event_store)
        self._port = port
        self._pre_hook = pre_tool_hook or NoOpPreToolHook()
        self._post_hook = post_tool_hook or NoOpPostToolHook()
        self._access_policy = access_policy
        self._approval_callback = approval_callback
        self._sandbox_enforcement = sandbox_enforcement or SandboxEnforcementRouter(
            enabled=False,
        )
        self._tool_rules = tool_rules or []
        # T1.3: chains take precedence over the legacy single-hook slots when
        # supplied. Legacy fields remain so existing callers stay green; the
        # chains parameter is the path forward.
        self._middleware_chains = middleware_chains or {}
        # T3.3: per-session monotonic-denial tracker. When a tool call has
        # been denied, the same tool_call_id is refused without re-running
        # the policy pipeline.
        self._denial_tracker = denial_tracker

    async def _emit_channel_write_rejected(
        self,
        *,
        execution_context: dict | None,
        tool_call_id: str,
        tool_name: str,
        reason: str,
        source: str,
    ) -> None:
        """T3.5 — append a ``CHANNEL_WRITE_REJECTED`` event for audit.

        The event is folded-skipped; it does not affect channel state. It
        exists so the ``MONOTONIC.DENIAL`` invariant can verify that a
        later ``TOOL_CALL`` for the same ``tool_call_id`` is a resurrection.
        """
        from hecate.engine.eventstore import CURRENT_LOG_SCHEMA_VERSION, Event, EventType

        if self._event_store is None or not execution_context:
            return
        await self._event_store.append(
            Event(
                session_id=execution_context["session_id"],
                superstep=execution_context.get("superstep", 0),
                event_type=EventType.CHANNEL_WRITE_REJECTED,
                node_id=None,
                trace_id=execution_context.get("trace_id"),
                payload={
                    "channel": "tool_execution",
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "reason": reason,
                    "source": source,
                    "log_schema_version": CURRENT_LOG_SCHEMA_VERSION,
                },
            )
        )

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
        tc_id: str = "",
        execution_context: dict | None = None,
    ) -> AccessDecision | None:
        """Evaluate tool access policy if configured.

        Returns None when no policy is configured (backward compatible),
        allowing all tools to execute as before.
        """
        if self._access_policy is None:
            return None

        # T3.3 (guardrail-upgrade-trio): monotonic-denial check. A denied call
        # stays denied within the session — no re-evaluation, no approval
        # retry. The tracker is supplied via the constructor.
        if getattr(self, "_denial_tracker", None) is not None and tc_id and self._denial_tracker.is_denied(tc_id):
            return AccessDecision.DENY

        tool_meta: dict[str, Any] = {
            "risk_level": context.get("risk_level", "low"),
            "approval_required": context.get("approval_required", False),
            "sandbox_enabled": context.get("sandbox_enabled", False),
            "name": tool_name,
        }
        # T0.2: prefer caller-supplied rules from context; fall back to the
        # rules bound at construction time by WorkflowExecutionService.
        rules: list[ToolRule] = context.get("tool_rules") or self._tool_rules
        eval_context: dict[str, Any] = {"tool_name": tool_name}
        if "workspace_root" in context:
            eval_context["workspace_root"] = context["workspace_root"]
        # T0.2 (T2.4): thread tenant attribution into the decision emitter so
        # ``ToolDecisionModel`` rows carry workspace / session / agent / user.
        if self._event_store and execution_context:
            eval_context.setdefault("session_id", execution_context.get("session_id"))
            eval_context.setdefault("agent_id", execution_context.get("agent_id"))
            eval_context.setdefault("workspace_id", execution_context.get("workspace_id"))
            eval_context.setdefault("on_behalf_of_user", execution_context.get("on_behalf_of_user"))
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

        access_decision = self._check_access(name, arguments, context, tc_id=tc_id, execution_context=execution_context)
        if access_decision is not None:
            if access_decision == AccessDecision.DENY:
                # T3.3: record denial so subsequent identical calls are
                # refused without re-evaluating the policy pipeline.
                if getattr(self, "_denial_tracker", None) is not None and tc_id:
                    self._denial_tracker.deny(tc_id)
                # T3.5: emit CHANNEL_WRITE_REJECTED for audit (fold-skipped).
                await self._emit_channel_write_rejected(
                    execution_context=execution_context,
                    tool_call_id=tc_id,
                    tool_name=name,
                    reason="access_policy_deny",
                    source="tool_access_policy",
                )
                return {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": "Tool denied by access policy",
                    "is_error": True,
                }
            if access_decision == AccessDecision.REQUIRE_APPROVAL:
                if self._approval_callback is None:
                    if getattr(self, "_denial_tracker", None) is not None and tc_id:
                        self._denial_tracker.deny(tc_id)
                    await self._emit_channel_write_rejected(
                        execution_context=execution_context,
                        tool_call_id=tc_id,
                        tool_name=name,
                        reason="no_answerer",
                        source="approval_callback",
                    )
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

        # Check sandbox enforcement routing — when enabled, shell tools
        # with EXECUTE_SANDBOX route to DockerEnvironment.exec_shell().
        route_to_environment = self._sandbox_enforcement.should_route_to_environment(
            tool_name=name,
            decision=access_decision,
            sandbox_enabled=context.get("sandbox_enabled", False),
        )

        # Pre-tool hook (chain takes precedence; legacy hook is the fallback).
        from hecate.engine.middleware import Phase

        pre_chain = self._middleware_chains.get(Phase.TOOL_PRE_EXECUTE)
        if pre_chain is not None:
            # Build the chain's terminal handler as the actual execution entry
            # point — but the chain runs BEFORE execution, so its terminal
            # handler is a no-op that returns the data untouched. The real
            # execution happens below.
            async def _passthrough(data):
                return data

            pre_chain.set_handler(_passthrough)
            pre_data = {"name": name, "arguments": arguments, "context": context}
            pre_decision, pre_result = await pre_chain.run(pre_data)
            if pre_decision.action == GuardrailAction.BLOCK:
                logger.info(
                    "PreTool chain blocked tool '%s': stage=%s reason=%s",
                    name,
                    pre_decision.stage_id,
                    pre_decision.reason,
                )
                if getattr(self, "_denial_tracker", None) is not None and tc_id:
                    self._denial_tracker.deny(tc_id)
                return {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": f"Tool blocked: {pre_decision.reason}",
                    "is_error": True,
                }
        elif ToolMatcher.match(name, self._pre_hook.matcher):
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
            from hecate.engine.eventstore import CURRENT_LOG_SCHEMA_VERSION

            await self._event_store.append(
                Event(
                    session_id=execution_context["session_id"],
                    superstep=execution_context["superstep"],
                    event_type=EventType.TOOL_CALL,
                    node_id=None,
                    trace_id=execution_context.get("trace_id"),
                    payload={
                        "tool_name": name,
                        "arguments": arguments,
                        "tool_call_id": tc_id,
                        "log_schema_version": CURRENT_LOG_SCHEMA_VERSION,
                    },
                )
            )
        try:
            if use_sandbox:
                from hecate.engine.environment_volumes import resolve_environment_volumes

                sandbox_context = dict(context) if context else {}
                env = execution_context.get("environment") if execution_context else None
                sandbox_context["_sandbox_volumes"] = resolve_environment_volumes(env)
                if route_to_environment:
                    sandbox_context["_sandbox_enforcement"] = True
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
            from hecate.engine.eventstore import CURRENT_LOG_SCHEMA_VERSION

            await self._event_store.append(
                Event(
                    session_id=execution_context["session_id"],
                    superstep=execution_context["superstep"],
                    event_type=EventType.TOOL_RESULT,
                    node_id=None,
                    trace_id=execution_context.get("trace_id"),
                    payload={
                        "tool_name": name,
                        "result_length": len(str(result)),
                        "tool_call_id": tc_id,
                        "log_schema_version": CURRENT_LOG_SCHEMA_VERSION,
                    },
                )
            )

        if span_ctx:
            await self._port.end_span(
                span_ctx.span_id,
                output_data={"result_length": len(str(result))},
            )

        # Post-tool hook (chain takes precedence; legacy hook is the fallback).
        post_chain = self._middleware_chains.get(Phase.TOOL_RESULT)
        if post_chain is not None:

            async def _passthrough2(data):
                return data

            post_chain.set_handler(_passthrough2)
            post_data = {"name": name, "result": result, "context": context}
            post_decision, post_result = await post_chain.run(post_data)
            if post_decision.action == GuardrailAction.BLOCK:
                logger.info(
                    "PostTool chain sanitized tool '%s': stage=%s reason=%s",
                    name,
                    post_decision.stage_id,
                    post_decision.reason,
                )
                if getattr(self, "_denial_tracker", None) is not None and tc_id:
                    self._denial_tracker.deny(tc_id)
                return {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": f"Result sanitized: {post_decision.reason}",
                }
            if (
                post_decision.action == GuardrailAction.SANITIZE
                and post_decision.modified_data
                and "result" in post_decision.modified_data
            ):
                result = post_decision.modified_data["result"]
        elif ToolMatcher.match(name, self._post_hook.matcher):
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
