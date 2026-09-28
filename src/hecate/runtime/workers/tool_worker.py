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
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
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
    SideEffectClass,
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
    arguments, permission denial, missing tool) reports a failure — which
    still does not prove a non-idempotent side effect was skipped.
    Deliberately narrow — prefer human review over blind retry.
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


class ToolExecutionState(StrEnum):
    """Recovery state of a dispatched tool execution (plan G2).

    ``claimed`` is the crash window — TOOL_CALL persisted, outcome missing —
    and is deliberately distinct from ``never_started``; ``store_unavailable``
    is a failed lookup, which must never degrade to "no record".
    """

    NEVER_STARTED = "never_started"
    CLAIMED = "claimed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STORE_UNAVAILABLE = "store_unavailable"


@dataclass(frozen=True)
class ToolExecutionResolution:
    """Outcome of a recovery lookup for one ``execution_id``."""

    state: ToolExecutionState
    arguments_digest: str | None = None
    result_digest: str | None = None


def tool_arguments_digest(arguments: Any) -> str:
    """Stable digest of tool arguments for action-key conflict detection.

    Canonical JSON (sorted keys, compact separators) so equivalent payloads
    hash identically regardless of key order; arguments that cannot be
    serialized degrade to the empty-arguments digest.
    """
    try:
        canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        canonical = "{}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def resolve_tool_execution_state(event_store: Any, session_id: Any, execution_id: str) -> ToolExecutionResolution:
    """Resolve the recovery state of a tool execution from the event log.

    Consumes both ``TOOL_CALL`` (the claim) and ``TOOL_RESULT`` (the
    receipt) so recovery decisions rest on recorded state instead of the
    absence of records. The latest TOOL_RESULT wins when retries produced
    several receipts; legacy events without ``arguments_digest`` resolve
    with ``None`` (conflict checking is skipped, not guessed).
    """
    if event_store is None:
        return ToolExecutionResolution(state=ToolExecutionState.NEVER_STARTED)
    try:
        events = await event_store.get_events(session_id)
    except Exception:
        logger.warning("Tool recovery lookup failed; state is store_unavailable", exc_info=True)
        return ToolExecutionResolution(state=ToolExecutionState.STORE_UNAVAILABLE)
    claim: dict[str, Any] | None = None
    receipt: dict[str, Any] | None = None
    for event in events:
        payload = event.payload
        if payload.get("execution_id") != execution_id:
            continue
        if event.event_type is EventType.TOOL_CALL:
            claim = payload
        elif event.event_type is EventType.TOOL_RESULT:
            receipt = payload
    if receipt is not None:
        status = receipt.get("status")
        if status == RECEIPT_SUCCEEDED:
            state = ToolExecutionState.SUCCEEDED
        elif status == RECEIPT_UNKNOWN:
            state = ToolExecutionState.OUTCOME_UNKNOWN
        else:
            state = ToolExecutionState.FAILED
        return ToolExecutionResolution(
            state=state,
            arguments_digest=receipt.get("arguments_digest") or (claim or {}).get("arguments_digest"),
            result_digest=receipt.get("result_digest"),
        )
    if claim is not None:
        return ToolExecutionResolution(
            state=ToolExecutionState.CLAIMED,
            arguments_digest=claim.get("arguments_digest"),
        )
    return ToolExecutionResolution(state=ToolExecutionState.NEVER_STARTED)


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

    @staticmethod
    def _withheld_result(tc_id: str, content: str) -> dict[str, Any]:
        """Terminal tool-result message for a dispatch that must not run."""
        return {"role": "tool", "tool_call_id": tc_id, "content": content, "is_error": True}

    async def _append_tool_call(
        self,
        *,
        execution_context: dict,
        tool_name: str,
        arguments: dict,
        arguments_digest: str,
        tool_call_id: str,
        execution_id: str,
        side_effect_class: str,
    ) -> None:
        """Record the claim for an execution (TOOL_CALL event)."""
        from hecate.runtime.eventstore import CURRENT_LOG_SCHEMA_VERSION

        await self._event_store.append(
            Event(
                session_id=execution_context["session_id"],
                superstep=execution_context["superstep"],
                event_type=EventType.TOOL_CALL,
                node_id=None,
                trace_id=execution_context.get("trace_id"),
                payload={
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "arguments_digest": arguments_digest,
                    "tool_call_id": tool_call_id,
                    "execution_id": execution_id,
                    "side_effect_class": side_effect_class,
                    "log_schema_version": CURRENT_LOG_SCHEMA_VERSION,
                },
            )
        )

    async def _recovery_outcome(
        self,
        *,
        session_key: Any,
        execution_id: str,
        tc_id: str,
        arguments_digest: str,
        classification: SideEffectClass,
        messages: list[dict],
        claim: bool,
        execution_context: dict | None = None,
        tool_name: str = "",
        arguments: dict | None = None,
    ) -> dict[str, Any] | None:
        """Resolve the execution's recovery state into an action.

        Returns a terminal tool-result message when the dispatch must not
        execute (succeeded backfill, review markers, digest conflict), or
        None when execution may proceed. With ``claim=True`` — the call
        inside the session event lock — a ``never_started`` resolution is
        claimed by appending the TOOL_CALL event; with ``claim=False`` this
        is a read-only pre-filter. Decisions by state:

        - succeeded → backfill the real result from channel history; when
          the content never reached the channel, return an explicit
          reconciliation marker (never fabricated content, never re-run)
        - outcome_unknown → human review
        - claimed → class-safe re-execution (readonly/idempotent) under the
          same execution id; everything else stops for review
        - failed → ``should_auto_retry`` (readonly/idempotent only)
        - store_unavailable → readonly proceeds; side effects fail closed
        """
        if self._event_store is None or not session_key:
            return None
        resolution = await resolve_tool_execution_state(self._event_store, session_key, execution_id)
        if resolution.arguments_digest is not None and resolution.arguments_digest != arguments_digest:
            logger.warning(
                "Tool execution %s arguments digest mismatch — conflict, not executed",
                execution_id,
            )
            return self._withheld_result(
                tc_id,
                "[conflict] arguments differ from the recorded execution for this call id; execution withheld",
            )
        state = resolution.state
        if state is ToolExecutionState.NEVER_STARTED:
            if claim and execution_context is not None and arguments is not None:
                await self._append_tool_call(
                    execution_context=execution_context,
                    tool_name=tool_name,
                    arguments=arguments,
                    arguments_digest=arguments_digest,
                    tool_call_id=tc_id,
                    execution_id=execution_id,
                    side_effect_class=classification.value,
                )
            return None
        if state is ToolExecutionState.SUCCEEDED:
            for msg in reversed(messages):
                if msg.get("role") == "tool" and msg.get("tool_call_id") == tc_id:
                    return {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": msg.get("content", ""),
                    }
            digest_suffix = f" (result_digest={resolution.result_digest})" if resolution.result_digest else ""
            logger.warning(
                "Tool execution %s recorded succeeded but result content is unavailable — reconciliation required",
                execution_id,
            )
            return self._withheld_result(
                tc_id,
                "[reconciliation required] execution recorded succeeded but recorded result "
                f"content is unavailable{digest_suffix}; not re-executed",
            )
        if state is ToolExecutionState.OUTCOME_UNKNOWN:
            return self._withheld_result(
                tc_id,
                "[needs review] previous execution outcome indeterminate; retry withheld",
            )
        if state is ToolExecutionState.CLAIMED:
            if should_auto_retry(classification, None):
                return None
            return self._withheld_result(
                tc_id,
                "[needs review] dispatch recorded but no outcome receipt (interrupted execution); retry withheld",
            )
        if state is ToolExecutionState.FAILED:
            if should_auto_retry(classification, RECEIPT_FAILED):
                return None
            return self._withheld_result(
                tc_id,
                "[needs review] previous execution failed; retry withheld for this side-effect class",
            )
        # STORE_UNAVAILABLE: fail closed for anything side-effecting.
        if classification is SideEffectClass.READONLY:
            return None
        return self._withheld_result(
            tc_id,
            "[withheld] tool receipt store unavailable; side-effecting execution withheld",
        )

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
        side_effect_class = classify(name)
        arguments_digest = tool_arguments_digest(arguments)
        messages = context.get("messages", []) if context else []

        # Recovery pre-filter (read-only, no claim): terminal states resolve
        # before access checks so a replay never re-triggers approval or
        # denial bookkeeping for an already-resolved execution. Executable
        # states fall through to the authoritative locked check below.
        recovery = await self._recovery_outcome(
            session_key=session_key,
            execution_id=execution_id,
            tc_id=tc_id,
            arguments_digest=arguments_digest,
            classification=side_effect_class,
            messages=messages,
            claim=False,
        )
        if recovery is not None:
            return recovery

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
        # Authoritative recovery + claim, inside the session event lock:
        # re-resolve against the log and claim never-started executions so
        # concurrent dispatches (duplicate workers) admit at most one first
        # executor. A failed lock must not silently re-run side effects.
        if self._event_store is not None and execution_context and session_key:
            try:
                async with self._event_store.acquire_event_lock(session_key):
                    recovery = await self._recovery_outcome(
                        session_key=session_key,
                        execution_id=execution_id,
                        tc_id=tc_id,
                        arguments_digest=arguments_digest,
                        classification=side_effect_class,
                        messages=messages,
                        claim=True,
                        execution_context=execution_context,
                        tool_name=name,
                        arguments=arguments,
                    )
            except Exception:
                logger.warning("Event lock failed for tool execution %s — fail closed", execution_id, exc_info=True)
                recovery = (
                    None
                    if side_effect_class is SideEffectClass.READONLY
                    else self._withheld_result(
                        tc_id,
                        "[withheld] tool receipt store unavailable; side-effecting execution withheld",
                    )
                )
            if recovery is not None:
                if span_ctx:
                    await self._port.end_span(span_ctx.span_id, output_data={"withheld": "recovery"})
                return recovery
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
                            "arguments_digest": arguments_digest,
                            "side_effect_class": side_effect_class.value,
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
                        "arguments_digest": arguments_digest,
                        "side_effect_class": side_effect_class.value,
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
