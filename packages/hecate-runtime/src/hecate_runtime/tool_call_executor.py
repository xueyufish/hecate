"""Shared single-tool-call execution component (step7 extraction).

``ToolCallExecutor`` carries the complete governance chain for ONE tool call —
argument parsing and digest, recovery pre-filter and authoritative claim,
access policy, approval, pre/post middleware, sandbox routing, the
``ActionLedgerHook`` mirror, TOOL_RESULT receipts, evidence capture and the
unknown-outcome stop — so callers execute a governed tool call without
constructing a Pregel graph, ChannelManager or ``Worker``. ``ToolWorker``
delegates each extracted call here; the batch path (message extraction,
concurrent scheduling, ordering, citation marking) stays with the worker.

Provenance contract for standalone callers (``execution_context`` dict keys):
Provide ``session_id`` and a nonempty tool-call ID for stable recovery;
legacy calls without either retain their random execution-ID fallback.
``superstep``/``trace_id`` are optional event fields (defaults 0/None);
``agent_id``/``workspace_id``/``environment``/``evidence_tracker`` use the
same keys as the graph path. ``node_id`` is the
caller's node label for evidence provenance (standalone callers pass their
own label). No new extension point: concrete class, injected deps only.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Literal

from hecate_runtime.accepted_decision import DecisionOwnership, DecisionValidationError, RunRef, canonical_json
from hecate_runtime.action_ledger import (  # noqa: F401 - compatibility re-exports
    ActionClaimVerdict,
    ActionLedgerHook,
    ToolExecutionResolution,
    ToolExecutionState,
    recovery_decision,
    resolve_tool_execution_state,
    stable_execution_id,
    tool_arguments_digest,
)
from hecate_runtime.errors import ExecutionStoppedError
from hecate_runtime.eventstore import Event, EventType
from hecate_runtime.guardrail import (
    GuardrailAction,
    NoOpPostToolHook,
    NoOpPreToolHook,
    PostToolHook,
    PreToolHook,
)
from hecate_runtime.ports import RuntimePort
from hecate_runtime.sandbox_router import SandboxEnforcementRouter
from hecate_runtime.tool_access import (
    AccessDecision,
    ApprovalCallback,
    ToolAccessPolicy,
    ToolRule,
)
from hecate_runtime.tool_matcher import ToolMatcher
from hecate_runtime.tool_side_effects import (
    RECEIPT_FAILED,
    RECEIPT_SUCCEEDED,
    RECEIPT_UNKNOWN,
    SideEffectClass,
    classify,
)

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

    # step7: governance claim-ledger refusals (duplicate in-flight invocation,
    # receipt-write failure after a successful business call) flag themselves
    # with ``outcome_unknown = True`` — duck-typed so the runtime keeps zero
    # external imports. The prior call may have completed remotely; only human
    # reconciliation resolves it.
    if getattr(exc, "outcome_unknown", False):
        return True
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


# Severity used to merge the two recovery authorities (event log vs ledger):
# the least-executable verdict wins so a conservative source is never
# overridden. SUCCEEDED ranks highest for backfill (no re-execution either
# way); unknown/unavailable outrank claimed; never_started loses to all.
_SEVERITY = {
    ToolExecutionState.SUCCEEDED: 5,
    ToolExecutionState.OUTCOME_UNKNOWN: 4,
    ToolExecutionState.STORE_UNAVAILABLE: 4,
    ToolExecutionState.CLAIMED: 3,
    ToolExecutionState.FAILED: 2,
    ToolExecutionState.NEVER_STARTED: 0,
}


def _merge_resolutions(
    event: ToolExecutionResolution | None, ledger: ToolExecutionResolution | None
) -> ToolExecutionResolution | None:
    if event is None:
        return ledger
    if ledger is None:
        return event
    if event.state is ledger.state:
        if event.result_content is not None:
            return event
        if ledger.result_content is not None:
            return ledger
        return event if event.result_digest is not None else ledger
    return event if _SEVERITY[event.state] >= _SEVERITY[ledger.state] else ledger


def _input_request_value(result: Any) -> Any:
    # The explicit business envelope uses the same typed value during
    # first-run and ledger-replay governance. Ordinary tool text is opaque.
    if isinstance(result, str):
        try:
            value = json.loads(result)
        except ValueError:
            return result
        if isinstance(value, dict) and value.get("status") == "input_required":
            return value
    return result


def _tool_result_content(result: Any) -> str:
    # Explicit input envelopes must survive the same JSON round trip used
    # by durable ledger backfill, after post-tool governance on both paths.
    if isinstance(result, dict) and result.get("status") == "input_required":
        return json.dumps(result, ensure_ascii=False)
    return str(result)


@dataclass(frozen=True)
class AcceptedToolCallOutcome:
    """Semantic outcome from control flow, without parsing tool message text."""

    status: Literal["succeeded", "failed", "withheld"]
    result: dict[str, Any]


@dataclass
class _AcceptedCall:
    run_ref: RunRef
    ownership: DecisionOwnership
    status: Literal["succeeded", "failed", "withheld"] = "withheld"


class ToolCallExecutor:
    """Executes one governed tool call through the injected port and governance.

    Same injected dependencies and behavior as the ``ToolWorker`` single-call
    path it was extracted from; see the module docstring for the standalone
    provenance contract.
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
        action_hook: ActionLedgerHook | None = None,
    ) -> None:
        self._event_store = event_store
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
        # Step6 durable-execution-core: optional persistent action-ledger
        # mirror. When present, intent/claim/outcome are mirrored to the
        # ledger and its resolution participates in recovery decisions —
        # the durable authority that survives process restarts.
        self._action_hook = action_hook

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
        from hecate_runtime.eventstore import CURRENT_LOG_SCHEMA_VERSION, Event, EventType

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
        from hecate_runtime.eventstore import CURRENT_LOG_SCHEMA_VERSION

        await self._event_store.append(
            Event(
                session_id=execution_context["session_id"],
                superstep=execution_context.get("superstep", 0),
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
        ledger_claimed: bool = False,
    ) -> dict[str, Any] | None:
        """Resolve the execution's recovery state into an action.

        Returns a terminal tool-result message when the dispatch must not
        execute (succeeded backfill, review markers, digest conflict), or
        None when execution may proceed. Two authorities feed one decision
        table (``recovery_decision``): the runtime event log and, when
        configured, the persistent action ledger — the latter survives
        process restarts. With ``claim=True`` — the call inside the session
        event lock — a ``never_started`` resolution is claimed by appending
        the TOOL_CALL event; with ``claim=False`` this is a read-only
        pre-filter.
        """

        if (self._event_store is None and self._action_hook is None) or not session_key:
            return None
        resolution: ToolExecutionResolution | None = None
        if self._event_store is not None:
            resolution = await resolve_tool_execution_state(self._event_store, session_key, execution_id)
        hook_resolution: ToolExecutionResolution | None = None
        if self._action_hook is not None and not ledger_claimed:
            hook_resolution = await self._hook_resolution(session_key, execution_id)
        merged = _merge_resolutions(resolution, hook_resolution)
        if merged is not None:
            recorded_content = merged.result_content
            # Ledger content is the PRE-governance first-run result and must
            # re-run output governance before reaching the model. Channel-log
            # content is already the first-run governed message and passes
            # through untouched — and when the context already carries this
            # call's tool message, recovery_decision returns that governed
            # copy instead of the ledger content, so it must not be marked.
            has_channel_message = any(
                isinstance(m, dict) and m.get("role") == "tool" and m.get("tool_call_id") == tc_id for m in messages
            )
            from_ledger = recorded_content is not None and not has_channel_message
            if (
                recorded_content is None
                and merged.state is ToolExecutionState.SUCCEEDED
                and self._event_store is not None
                and not has_channel_message
            ):
                # The channel lost the receipt (cross-turn restore or a
                # reconnect); the session log's messages-channel writes still
                # carry the real content — recover it instead of handing the
                # model a placeholder.
                recorded_content = await self._log_recorded_content(session_key, tc_id)
            decision = recovery_decision(
                merged,
                tc_id=tc_id,
                tool_name=tool_name,
                arguments_digest=arguments_digest,
                classification=classification,
                messages=messages,
                recorded_content=recorded_content,
            )
            if decision is not None:
                result = self._checked_recovery_result(decision, execution_id)
                if result is not None and from_ledger and self._is_governed_backfill(result):
                    result = {**result, "_replay_govern": True}
                return result
        if claim and execution_context is not None and arguments is not None and self._event_store is not None:
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

    async def _log_recorded_content(self, session_key: Any, tc_id: str) -> str | None:
        """Recover a tool result's real content from the session channel log.

        The messages channel is write-logged with full lists; the newest
        list carrying this call's tool message holds the receipt content
        the channel lost. Store failures return None (the decision table
        then applies its unavailable-content semantics)."""
        if self._event_store is None:
            return None
        from hecate_runtime.eventstore import EventType

        try:
            events = await self._event_store.get_events(session_key)
        except Exception:
            logger.warning("Tool receipt log lookup failed", exc_info=True)
            return None
        latest: dict[str, Any] | None = None
        for event in events:
            if event.event_type is not EventType.CHANNEL_WRITE:
                continue
            payload = event.payload or {}
            if payload.get("channel") != "messages":
                continue
            value = payload.get("value")
            if not isinstance(value, list):
                continue
            for item in value:
                if (
                    isinstance(item, dict)
                    and item.get("role") == "tool"
                    and item.get("tool_call_id") == tc_id
                    and not item.get("is_error")
                    and isinstance(item.get("content"), str)
                ):
                    # Older logs can contain withheld recovery markers. They
                    # are not receipts for the succeeded action; stripping
                    # is_error would let the model reason on the placeholder.
                    latest = item
        if latest is None:
            return None
        content = latest.get("content")
        return content if isinstance(content, str) else None

    async def _hook_resolution(self, session_key: Any, execution_id: str) -> ToolExecutionResolution:
        """Resolve from the persistent ledger; store failure is explicit."""

        try:
            return await self._action_hook.resolve(session_id=str(session_key), execution_id=execution_id)
        except Exception:
            logger.warning("Action ledger lookup failed — store_unavailable", exc_info=True)
            return ToolExecutionResolution(state=ToolExecutionState.STORE_UNAVAILABLE)

    async def _hook_claim(
        self,
        *,
        session_key: Any,
        execution_id: str,
        tc_id: str,
        tool_name: str,
        arguments: dict,
        arguments_digest: str,
        side_effect_class: SideEffectClass,
        messages: list[dict],
        accepted: _AcceptedCall | None = None,
    ) -> dict[str, Any] | None:
        """Persist intent + atomically claim via the ledger hook.

        Returns a withheld/blocked result when the claim must not proceed
        (conflict, lost claim, ledger failure on a side-effecting class);
        None when this executor may dispatch.
        """

        hook = self._action_hook
        if hook is None:  # pragma: no cover - caller guards on hook presence
            return None
        try:
            if accepted is not None:
                await hook.validate_accepted_dispatch(
                    run_ref=accepted.run_ref, ownership=accepted.ownership, execution_id=execution_id
                )
            verdict = await hook.record_claim(
                session_id=str(session_key),
                execution_id=execution_id,
                tool_call_id=tc_id,
                tool_name=tool_name,
                arguments=arguments,
                arguments_digest=arguments_digest,
                side_effect_class=side_effect_class.value,
            )
        except Exception:
            if accepted is not None:
                raise
            logger.warning("Action ledger claim failed — fail closed", exc_info=True)
            if side_effect_class is SideEffectClass.READONLY:
                return None
            return self._withheld_result(
                tc_id, "[withheld] action ledger unavailable; side-effecting execution withheld"
            )
        if verdict.conflict is not None:
            return self._withheld_result(tc_id, verdict.conflict)
        if verdict.claimed:
            return None
        resolution = verdict.resolution
        if resolution is None:  # pragma: no cover - defensive
            raise ExecutionStoppedError("needs_review", "ledger verdict missing; retry withheld", execution_id)
        decision = recovery_decision(
            resolution,
            tc_id=tc_id,
            tool_name=tool_name,
            arguments_digest=arguments_digest,
            classification=side_effect_class,
            messages=messages,
            recorded_content=resolution.result_content,
        )
        result = self._checked_recovery_result(decision, execution_id)
        has_channel_message = any(
            isinstance(m, dict) and m.get("role") == "tool" and m.get("tool_call_id") == tc_id for m in messages
        )
        if result is not None and not has_channel_message and self._is_governed_backfill(result):
            result = {**result, "_replay_govern": True}
        return result

    @staticmethod
    def _checked_recovery_result(decision: dict[str, Any] | None, execution_id: str) -> dict[str, Any] | None:
        if decision is not None and decision.get("is_error"):
            content = str(decision.get("content", ""))
            if content.startswith("[needs review]"):
                raise ExecutionStoppedError("needs_review", content, execution_id)
            if content.startswith("[reconciliation required]"):
                raise ExecutionStoppedError("reconciliation_required", content, execution_id)
        return decision

    @staticmethod
    def _is_governed_backfill(recovery: dict[str, Any]) -> bool:
        """True for a succeeded ledger backfill that must re-run governance.

        Exactly the shape ``recovery_decision`` builds for succeeded-with-
        content: a tool message without ``is_error``. Withheld placeholders
        and failure notices carry ``is_error``; review/reconciliation
        markers never return as dicts — they raise before reaching callers.
        """
        return (
            isinstance(recovery, dict)
            and recovery.get("role") == "tool"
            and not recovery.get("is_error")
            and isinstance(recovery.get("content"), str)
        )

    async def _govern_replayed_result(
        self,
        *,
        name: str,
        tc_id: str,
        execution_id: str,
        content: str,
        base_context: dict | None,
    ) -> dict[str, Any]:
        """Re-apply first-run PostTool governance to a replayed receipt.

        The ledger mirrors the PRE-governance result on first execution, so
        returning its content as-is would re-expose text the first run
        blocked or sanitized. Ledger-marked backfills funnel here and replay
        the first-execution pipeline: the ``TOOL_RESULT`` middleware chain
        first, the legacy ``PostToolHook`` as fallback. Unlike the first
        run, a BLOCK does not touch denial bookkeeping — the action is
        already settled in the ledger and recovery must not re-trigger
        denial/approval state. Governance failures propagate; raw content is
        never returned as a fallback.
        """
        from hecate_runtime.middleware import Phase

        content = _input_request_value(content)
        context = dict(base_context or {})
        context["tool_call_id"] = tc_id
        context["invocation_id"] = execution_id
        post_chain = self._middleware_chains.get(Phase.TOOL_RESULT)
        if post_chain is not None:

            async def _passthrough_replay(data):
                return data

            post_chain.set_handler(_passthrough_replay)
            post_decision, _post_result = await post_chain.run({"name": name, "result": content, "context": context})
            if post_decision.action == GuardrailAction.BLOCK:
                logger.info(
                    "PostTool chain governed replayed tool '%s': stage=%s reason=%s",
                    name,
                    post_decision.stage_id,
                    post_decision.reason,
                )
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
                content = post_decision.modified_data["result"]
        elif ToolMatcher.match(name, self._post_hook.matcher):
            post_result = await self._post_hook.on_post_tool_call(
                name=name,
                result=content,
                context=context,
            )
            if post_result.action == GuardrailAction.BLOCK:
                logger.info("PostToolHook governed replayed tool '%s': %s", name, post_result.reason)
                return {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": f"Result sanitized: {post_result.reason}",
                }
            if post_result.action == GuardrailAction.SANITIZE:
                if post_result.modified_data and "result" in post_result.modified_data:
                    content = post_result.modified_data["result"]
                else:
                    logger.warning("SANITIZE without modified_data for tool '%s'", name)
        return {
            "role": "tool",
            "tool_call_id": tc_id,
            "content": _tool_result_content(content),
        }

    async def _maybe_govern_recovery(
        self,
        recovery: dict[str, Any],
        *,
        name: str,
        tc_id: str,
        execution_id: str,
        base_context: dict | None,
    ) -> dict[str, Any]:
        """Govern the ledger-marked backfill; pass every other shape through.

        The ``_replay_govern`` marker is set only where the recovery content
        provably came from the durable ledger — channel-log content is
        already the first-run governed message and must not be governed a
        second time.
        """
        if not isinstance(recovery, dict) or not recovery.pop("_replay_govern", False):
            return recovery
        return await self._govern_replayed_result(
            name=name,
            tc_id=tc_id,
            execution_id=execution_id,
            content=recovery["content"],
            base_context=base_context,
        )

    async def _mirror_outcome(
        self,
        *,
        session_key: Any,
        execution_id: str,
        tc_id: str,
        tool_name: str,
        arguments_digest: str,
        status: str,
        result: Any = None,
        error: str | None = None,
        strict: bool = False,
    ) -> None:
        """Mirror the real outcome to the ledger; never undo the effect.

        A failed ledger write leaves the action claimed — recovery then
        fails closed (pending reconciliation) instead of silently re-running
        the side effect. The result still flows through the channel.
        """

        try:
            await self._action_hook.record_outcome(
                session_id=str(session_key),
                execution_id=execution_id,
                tool_call_id=tc_id,
                tool_name=tool_name,
                arguments_digest=arguments_digest,
                status=status,
                result=result,
                error=error,
            )
        except Exception as exc:
            if strict:
                raise ExecutionStoppedError(
                    "reconciliation_required", "action outcome was not durably recorded", execution_id
                ) from exc
            logger.warning(
                "Action ledger outcome write failed for %s — stays claimed, pending reconciliation",
                execution_id,
                exc_info=True,
            )

    async def execute_call(
        self, tool_call: dict, context: dict, execution_context: dict | None = None, node_id: str = ""
    ) -> dict[str, Any]:
        """Execute one legacy tool call with unchanged governance/recovery semantics."""
        return await self._execute_call(tool_call, context, execution_context, node_id)

    async def execute_accepted_call(
        self,
        tool_call: dict,
        context: dict,
        execution_context: dict,
        *,
        run_ref: RunRef,
        ownership: DecisionOwnership | None,
        node_id: str = "",
    ) -> AcceptedToolCallOutcome:
        """Require a Run-bound fenced ledger, then reuse the shared tool pipeline."""
        if self._action_hook is None or not isinstance(ownership, DecisionOwnership):
            raise DecisionValidationError("accepted actions require ownership and a fenced action ledger")
        accepted = _AcceptedCall(run_ref, ownership)
        try:
            result = await self._execute_call(tool_call, context, execution_context, node_id, accepted=accepted)
        except ExecutionStoppedError:
            raise
        except Exception as exc:
            if accepted.status != "withheld":
                raise ExecutionStoppedError("reconciliation_required", "action receipt could not be confirmed") from exc
            raise
        return AcceptedToolCallOutcome(accepted.status, result)

    async def _execute_call(
        self,
        tool_call: dict,
        context: dict,
        execution_context: dict | None = None,
        node_id: str = "",
        *,
        accepted: _AcceptedCall | None = None,
    ) -> dict[str, Any]:
        """Execute a single tool call with pre/post hooks.

        Args:
            tool_call: Dict with id, function/name, function/arguments.
            context: Channel snapshot for hook context.
            execution_context: Optional runtime execution context.
            node_id: Caller label for evidence provenance; no graph node is required.

        Returns:
            Tool result message dict.
        """
        tc_id = tool_call.get("id", "")
        func_info = tool_call.get("function", {})
        name = func_info.get("name", tool_call.get("name", "unknown"))
        arguments = func_info.get("arguments", tool_call.get("arguments", {}))

        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return self._withheld_result(tc_id, "Invalid tool arguments: expected a JSON object")
        if not isinstance(arguments, dict):
            return self._withheld_result(tc_id, "Invalid tool arguments: expected a JSON object")
        try:
            arguments_digest = tool_arguments_digest(arguments)
        except (TypeError, ValueError):
            return self._withheld_result(tc_id, "Invalid tool arguments: expected JSON-serializable values")

        # Server-assigned stable execution id — the recovery key. Derived
        # deterministically from (session, tool_call_id) so a resumed replay
        # of the same logical call lands on the SAME id and can find its
        # prior receipt; the LLM's tool_call_id may be empty or ephemeral
        # (then the id degenerates to random and receipt matching is simply
        # unavailable for that call).
        session_key = (execution_context or {}).get("session_id")
        execution_id = stable_execution_id(session_key, tc_id)
        accepted_arguments = canonical_json(arguments) if accepted is not None else None
        side_effect_class = classify(name)
        if accepted is not None:
            await self._action_hook.validate_accepted_dispatch(
                run_ref=accepted.run_ref, ownership=accepted.ownership, execution_id=execution_id
            )
        messages = [] if accepted is not None else (context.get("messages", []) if context else [])

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
            tool_name=name,
        )
        if recovery is not None:
            if accepted is not None and not recovery.get("is_error"):
                accepted.status = "succeeded"
            recovery = await self._maybe_govern_recovery(
                recovery, name=name, tc_id=tc_id, execution_id=execution_id, base_context=context
            )
            return recovery

        approval_ref = None
        context = dict(context or {})
        context["tool_call_id"] = tc_id
        context["invocation_id"] = execution_id
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
                approval_ref = approval.approval_ref
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
        from hecate_runtime.middleware import Phase

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

        if accepted is not None and canonical_json(arguments) != accepted_arguments:
            raise DecisionValidationError("middleware changed the accepted action arguments")
        # Execute tool
        span_ctx = await self._port.create_span(
            name=f"tool:{name}",
            attributes={"tool_name": name, "gen_ai.tool.name": name, "arguments": str(arguments)[:500]},
        )
        # Authoritative ledger claim (step6 durable-execution-core): persist
        # intent and claim atomically in the durable store FIRST — it is the
        # authority that survives restarts. A lost claim or a conflict
        # withholds here; readonly losers may proceed (replayable).
        if self._action_hook is not None and session_key:
            hook_decision = await self._hook_claim(
                session_key=session_key,
                execution_id=execution_id,
                tc_id=tc_id,
                tool_name=name,
                arguments=arguments,
                arguments_digest=arguments_digest,
                side_effect_class=side_effect_class,
                messages=messages,
                accepted=accepted,
            )
            if hook_decision is not None:
                if accepted is not None and not hook_decision.get("is_error"):
                    accepted.status = "succeeded"
                hook_decision = await self._maybe_govern_recovery(
                    hook_decision, name=name, tc_id=tc_id, execution_id=execution_id, base_context=context
                )
                if span_ctx:
                    await self._port.end_span(span_ctx.span_id, output_data={"withheld": "ledger"})
                return hook_decision

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
                        # The durable gate just granted this executor its claim.
                        # Re-reading that claim would mistake it for a crash.
                        ledger_claimed=self._action_hook is not None,
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
                if accepted is not None and not recovery.get("is_error"):
                    accepted.status = "succeeded"
                recovery = await self._maybe_govern_recovery(
                    recovery, name=name, tc_id=tc_id, execution_id=execution_id, base_context=context
                )
                if span_ctx:
                    await self._port.end_span(span_ctx.span_id, output_data={"withheld": "recovery"})
                return recovery
        if accepted is not None:
            await self._action_hook.validate_accepted_dispatch(
                run_ref=accepted.run_ref, ownership=accepted.ownership, execution_id=execution_id
            )
        if accepted is not None and canonical_json(arguments) != accepted_arguments:
            raise DecisionValidationError("accepted action arguments changed before dispatch")
        try:
            tool_start = time.monotonic()
            # Thread agent/workspace attribution into tool context so
            # agent-scoped builtin tools (load_skill) can enforce catalog
            # membership; harmless keys for everything else.
            tool_context: dict[str, Any] = dict(context) if context else {}
            tool_context["invocation_id"] = execution_id
            tool_context["ledger_owned"] = self._action_hook is not None or self._event_store is not None
            tool_context["approval_ref"] = approval_ref
            tool_context["tool_policy_rules"] = self._tool_rules
            if execution_context:
                tool_context.setdefault("agent_id", execution_context.get("agent_id"))
                tool_context.setdefault("workspace_id", execution_context.get("workspace_id"))
                # 4.13 recall tool: the agent environment + session id ride the
                # per-call context so offloaded blocks can be reloaded read-only.
                tool_context.setdefault("environment", execution_context.get("environment"))
                if execution_context.get("session_id") is not None:
                    tool_context.setdefault("session_id", str(execution_context.get("session_id")))
            if accepted is not None:
                accepted.status = "failed"
            if use_sandbox:
                from hecate_runtime.environment_volumes import resolve_environment_volumes

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
            status = RECEIPT_UNKNOWN if _is_indeterminate_error(e) else RECEIPT_FAILED
            if self._event_store and execution_context:
                from hecate_runtime.eventstore import CURRENT_LOG_SCHEMA_VERSION

                await self._event_store.append(
                    Event(
                        session_id=execution_context["session_id"],
                        superstep=execution_context.get("superstep", 0),
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
            if self._action_hook is not None and session_key:
                await self._mirror_outcome(
                    session_key=session_key,
                    execution_id=execution_id,
                    tc_id=tc_id,
                    tool_name=name,
                    arguments_digest=arguments_digest,
                    status=status,
                    error=str(e)[:500],
                    strict=accepted is not None,
                )
            if status == RECEIPT_UNKNOWN:
                raise ExecutionStoppedError("needs_review", str(e), action_ref=execution_id) from e
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
        if accepted is not None:
            accepted.status = "succeeded"
        # Memory tools flag weak/empty retrieval results; the escalation hint
        # processor consumes this marker at the next context assembly
        # (agent-memory-tools, retrieval escalation gating).
        if isinstance(result, dict) and result.get("low_signal"):
            from hecate_runtime.tool_names import MEMORY_TOOL_NAMES

            if name in MEMORY_TOOL_NAMES and execution_context is not None:
                execution_context["memory_retrieval_low_signal"] = True
        if self._event_store and execution_context:
            import hashlib as _hashlib

            from hecate_runtime.eventstore import CURRENT_LOG_SCHEMA_VERSION

            await self._event_store.append(
                Event(
                    session_id=execution_context["session_id"],
                    superstep=execution_context.get("superstep", 0),
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
        if self._action_hook is not None and session_key:
            await self._mirror_outcome(
                session_key=session_key,
                execution_id=execution_id,
                tc_id=tc_id,
                tool_name=name,
                arguments_digest=arguments_digest,
                status=RECEIPT_SUCCEEDED,
                result=result,
                strict=accepted is not None,
            )

        if span_ctx:
            await self._port.end_span(
                span_ctx.span_id,
                output_data={"result_length": len(str(result))},
                usage={"duration_ms": int((time.monotonic() - tool_start) * 1000)},
            )

        # Preserve the envelope type that durable replay gives the guards.
        result = _input_request_value(result)
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
            "content": _tool_result_content(result),
        }
