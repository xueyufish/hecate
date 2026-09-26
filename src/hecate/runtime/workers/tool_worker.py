"""Tool execution worker with guardrail hook support.

Parses tool calls from the messages channel, invokes PreToolHook before
execution, executes tools via RuntimePort, invokes PostToolHook after
execution, captures evidence, and writes tool result messages back to
channel_updates.

When the assistant proposes more than one tool call in a single turn, the
worker dispatches them concurrently via ``asyncio.gather`` so independent
calls (e.g. parallel searches) finish in the wall-clock time of the slowest
one. Result ordering is preserved — the channel receives one ``tool`` result
per call in the same order as the LLM emitted them.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from hecate.runtime.citation_provenance import (
    EVENT_NAME_REGISTERED,
    EXECUTION_CONTEXT_KEY,
    emit_citation_event,
    mark_tool_result,
)
from hecate.runtime.eventstore import Event, EventType
from hecate.runtime.guardrail import (
    GuardrailAction,
    NoOpPostToolHook,
    NoOpPreToolHook,
    PostToolHook,
    PreToolHook,
)
from hecate.runtime.ports import RuntimePort
from hecate.runtime.provenance_policy import resolve_citation_policy
from hecate.runtime.tool_access import (
    AccessDecision,
    ApprovalCallback,
    ToolAccessPolicy,
    ToolRule,
)
from hecate.runtime.tool_matcher import ToolMatcher
from hecate.runtime.tool_side_effects import (
    RECEIPT_FAILED,
    RECEIPT_SUCCEEDED,
    RECEIPT_UNKNOWN,
    classify,
    should_auto_retry,
)
from hecate.runtime.types import WorkerResult
from hecate.runtime.worker import Worker
from hecate.runtime.workers.sandbox_router import SandboxEnforcementRouter

logger = logging.getLogger(__name__)


def _is_indeterminate_error(exc: Exception) -> bool:
    """Whether an execution exception leaves the outcome unknowable.

    Timeout and connection failures are indeterminate: the tool may have
    completed remotely after the client gave up. Everything else (bad
    arguments, permission denial, missing tool) provably did not take
    effect. Deliberately narrow — prefer human review over blind retry.
    """
    import httpx

    return isinstance(
        exc,
        (
            TimeoutError,
            ConnectionError,
            httpx.TimeoutException,
            httpx.ConnectError,
        ),
    )


async def get_tool_receipt(event_store: Any, session_id: Any, execution_id: str) -> dict[str, Any] | None:
    """Read the TOOL_RESULT receipt for a tool execution.

    Returns the most recent receipt payload (with ``status`` and
    ``side_effect_class``) for the execution, or None when no receipt
    exists — e.g. log loss, which recovery must treat as indeterminate.
    """
    if event_store is None:
        return None
    receipt: dict[str, Any] | None = None
    try:
        events = await event_store.get_events(session_id)
    except Exception:
        # An unreadable store must not block execution: treat as no receipt
        # and let should_auto_retry apply its conservative defaults.
        logger.warning("Tool receipt lookup failed; treating as no receipt", exc_info=True)
        return None
    for event in events:
        if event.event_type is not EventType.TOOL_RESULT:
            continue
        if event.payload.get("execution_id") != execution_id:
            continue
        receipt = event.payload
    return receipt


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
        from hecate.runtime.eventstore import CURRENT_LOG_SCHEMA_VERSION, Event, EventType

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

        tool_results: list[dict[str, Any]] = await asyncio.gather(
            *(self._execute_single_tool(tc, channel_snapshot, execution_context, node_id) for tc in tool_calls)
        )

        # 1.3.5e citation provenance: mark at the single choke point where
        # tool results are written to history, so every result shape
        # (success, error, sanitized) is treated uniformly.
        await self._apply_citation_provenance(tool_results, node_config, execution_context, node_id)

        return WorkerResult(
            node_id=node_id,
            channel_updates={"messages": tool_results},
        )

    async def _apply_citation_provenance(
        self,
        tool_results: list[dict[str, Any]],
        node_config: dict,
        execution_context: dict | None,
        node_id: str,
    ) -> None:
        """Mark tool result messages with citation chunks (1.3.5e Stage 1).

        No-op unless a citation provenance manager rides the execution
        context and the resolved policy (node > agent) is enabled. String
        content below the minimum size passes through unmarked; non-string
        content passes through unmarked in this stage. The raw content is
        not preserved on the message — the registry carries chunk texts, and
        markers are plain text prefixes the providers accept.
        """
        if not execution_context:
            return
        manager = execution_context.get(EXECUTION_CONTEXT_KEY)
        if manager is None:
            return
        policy = resolve_citation_policy(node_config, getattr(manager, "agent_policy", None))
        if not policy.enabled:
            return
        registry = manager.registry_for(execution_context.get("session_id"))
        for msg in tool_results:
            content = msg.get("content")
            if not isinstance(content, str) or not content:
                continue
            marked, entries = mark_tool_result(
                content,
                registry,
                policy.chunk_granularity,
                policy.min_chunk_chars,
            )
            if not entries:
                continue
            msg["content"] = marked
            await emit_citation_event(
                execution_context=execution_context,
                node_id=node_id,
                event_name=EVENT_NAME_REGISTERED,
                payload={
                    "tool_call_id": msg.get("tool_call_id"),
                    "result_seq": entries[0].result_seq,
                    "chunks": [e.as_dict() for e in entries],
                },
            )

    def _capture_evidence(
        self,
        *,
        execution_context: dict | None,
        node_id: str,
        name: str,
        arguments: dict,
        result: Any,
        is_error: bool,
    ) -> None:
        """Capture one tool outcome into the run's EvidenceTracker (4.8).

        Best-effort by tracker contract; a no-op when no tracker is wired
        into the execution context.
        """
        if not execution_context:
            return
        tracker = execution_context.get("evidence_tracker")
        if tracker is None:
            return
        existing = tracker.match_existing(name, arguments)
        tracker.capture(
            tool_name=name,
            arguments=arguments,
            raw_content=result if isinstance(result, (str, dict)) else str(result),
            is_error=is_error,
            node_id=node_id,
            superstep=execution_context.get("superstep", 0),
            reused=existing is not None,
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
        node_id: str = "",
    ) -> dict[str, Any]:
        """Execute a single tool call with pre/post hooks.

        Args:
            tool_call: Dict with id, function/name, function/arguments.
            context: Channel snapshot for hook context.
            execution_context: Optional runtime execution context.
            node_id: Graph node dispatching this call (evidence provenance).

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

        # Server-assigned stable execution id — the recovery key. Derived
        # deterministically from (session, tool_call_id) so a resumed replay
        # of the same logical call lands on the SAME id and can find its
        # prior receipt; the LLM's tool_call_id may be empty or ephemeral
        # (then the id degenerates to random and receipt matching is simply
        # unavailable for that call).
        session_key = (execution_context or {}).get("session_id")
        if session_key and tc_id:
            execution_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"tool-exec:{session_key}:{tc_id}"))
        else:
            execution_id = str(uuid.uuid4())
        side_effect_class = classify(name).value

        # Recovery (unified-chat-execution): an existing receipt means this
        # dispatch is a resume replay of an already-attempted call — never
        # re-run it blindly. succeeded → skip; unknown/failed-unretryable →
        # human review. Retryable combinations fall through and re-execute
        # under the same execution_id (latest receipt wins).
        if self._event_store is not None and session_key:
            prior_receipt = await get_tool_receipt(self._event_store, session_key, execution_id)
            prior_status = (prior_receipt or {}).get("status")
            if prior_status is not None and not should_auto_retry(classify(name), prior_status):
                if prior_status == RECEIPT_SUCCEEDED:
                    logger.info(
                        "Tool '%s' execution %s already succeeded (resume) — skipping re-execution",
                        name,
                        execution_id,
                    )
                    return {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": "[recovered] result restored from execution receipt; not re-executed",
                    }
                logger.warning(
                    "Tool '%s' execution %s outcome %s — human review, not re-executed",
                    name,
                    execution_id,
                    prior_status,
                )
                return {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": "[needs review] previous execution outcome indeterminate; retry withheld",
                    "is_error": True,
                }

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
                self._capture_evidence(
                    execution_context=execution_context,
                    node_id=node_id,
                    name=name,
                    arguments=arguments,
                    result="Tool denied by access policy",
                    is_error=True,
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
                    self._capture_evidence(
                        execution_context=execution_context,
                        node_id=node_id,
                        name=name,
                        arguments=arguments,
                        result=f"Tool call rejected: {approval.reason}",
                        is_error=True,
                    )
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
        from hecate.runtime.middleware import Phase

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
            from hecate.runtime.eventstore import CURRENT_LOG_SCHEMA_VERSION

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
                        "execution_id": execution_id,
                        "side_effect_class": side_effect_class,
                        "log_schema_version": CURRENT_LOG_SCHEMA_VERSION,
                    },
                )
            )
        try:
            tool_start = time.monotonic()
            # Thread agent/workspace attribution into tool context so
            # agent-scoped builtin tools (load_skill) can enforce catalog
            # membership; harmless keys for everything else.
            tool_context: dict[str, Any] = dict(context) if context else {}
            if execution_context:
                tool_context.setdefault("agent_id", execution_context.get("agent_id"))
                tool_context.setdefault("workspace_id", execution_context.get("workspace_id"))
                # 4.13 recall tool: the agent environment + session id ride the
                # per-call context so offloaded blocks can be reloaded read-only.
                tool_context.setdefault("environment", execution_context.get("environment"))
                if execution_context.get("session_id") is not None:
                    tool_context.setdefault("session_id", str(execution_context.get("session_id")))
            if use_sandbox:
                from hecate.runtime.environment_volumes import resolve_environment_volumes

                sandbox_context = tool_context
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
                    context=tool_context,
                )
        except Exception as e:
            logger.warning("Tool '%s' execution failed: %s", name, e)
            if span_ctx:
                await self._port.end_span(span_ctx.span_id, output_data={"error": str(e)})
            # Receipt: the TOOL_CALL above must not stay orphaned. Timeout /
            # connection failures leave the outcome unknowable (the tool may
            # have completed remotely) — recorded as unknown so recovery
            # sends the case to human review instead of blindly retrying.
            if self._event_store and execution_context:
                from hecate.runtime.eventstore import CURRENT_LOG_SCHEMA_VERSION

                status = RECEIPT_UNKNOWN if _is_indeterminate_error(e) else RECEIPT_FAILED
                await self._event_store.append(
                    Event(
                        session_id=execution_context["session_id"],
                        superstep=execution_context["superstep"],
                        event_type=EventType.TOOL_RESULT,
                        node_id=None,
                        trace_id=execution_context.get("trace_id"),
                        payload={
                            "tool_name": name,
                            "tool_call_id": tc_id,
                            "execution_id": execution_id,
                            "side_effect_class": side_effect_class,
                            "status": status,
                            "error": str(e)[:500],
                            "log_schema_version": CURRENT_LOG_SCHEMA_VERSION,
                        },
                    )
                )
            self._capture_evidence(
                execution_context=execution_context,
                node_id=node_id,
                name=name,
                arguments=arguments,
                result=str(e),
                is_error=True,
            )
            return {
                "role": "tool",
                "tool_call_id": tc_id,
                "content": str(e),
                "is_error": True,
            }
        # Memory tools flag weak/empty retrieval results; the escalation hint
        # processor consumes this marker at the next context assembly
        # (agent-memory-tools, retrieval escalation gating).
        if isinstance(result, dict) and result.get("low_signal"):
            try:
                from hecate.tools.tool.builtin import get_memory_tool_names

                if name in get_memory_tool_names() and execution_context is not None:
                    execution_context["memory_retrieval_low_signal"] = True
            except ImportError:
                pass
        if self._event_store and execution_context:
            import hashlib as _hashlib

            from hecate.runtime.eventstore import CURRENT_LOG_SCHEMA_VERSION

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
                        "result_digest": _hashlib.sha256(str(result).encode()).hexdigest(),
                        "tool_call_id": tc_id,
                        "execution_id": execution_id,
                        "side_effect_class": side_effect_class,
                        "status": RECEIPT_SUCCEEDED,
                        "log_schema_version": CURRENT_LOG_SCHEMA_VERSION,
                    },
                )
            )

        if span_ctx:
            await self._port.end_span(
                span_ctx.span_id,
                output_data={"result_length": len(str(result))},
                usage={"duration_ms": int((time.monotonic() - tool_start) * 1000)},
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

        self._capture_evidence(
            execution_context=execution_context,
            node_id=node_id,
            name=name,
            arguments=arguments,
            result=result,
            is_error=False,
        )
        return {
            "role": "tool",
            "tool_call_id": tc_id,
            "content": str(result),
        }
