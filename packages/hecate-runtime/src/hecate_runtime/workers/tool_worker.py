"""Tool execution worker with guardrail hook support.

Extracts tool calls from the messages channel and dispatches them through the
shared single-call governance chain (``hecate_runtime.tool_call_executor``,
step7): each extracted call is delegated to ``ToolCallExecutor`` — the worker
keeps batch orchestration only. When the assistant proposes more than one tool
call in a single turn, the worker dispatches them concurrently via
``asyncio.gather`` so independent calls (e.g. parallel searches) finish in the
wall-clock time of the slowest one. Result ordering is preserved — the channel
receives one ``tool`` result per call in the same order as the LLM emitted them.
"""

from __future__ import annotations

import asyncio
from typing import Any

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
from hecate_runtime.citation_provenance import (
    EVENT_NAME_REGISTERED,
    EXECUTION_CONTEXT_KEY,
    emit_citation_event,
    mark_tool_result,
)
from hecate_runtime.guardrail import (
    PostToolHook,
    PreToolHook,
)
from hecate_runtime.ports import RuntimePort
from hecate_runtime.provenance_policy import resolve_citation_policy
from hecate_runtime.tool_access import (
    ApprovalCallback,
    ToolAccessPolicy,
    ToolRule,
)
from hecate_runtime.tool_call_executor import (  # noqa: F401 - compatibility re-exports
    ToolCallExecutor,
    _is_indeterminate_error,
    _merge_resolutions,
    get_tool_receipt,
)
from hecate_runtime.types import WorkerResult
from hecate_runtime.worker import Worker
from hecate_runtime.workers.sandbox_router import SandboxEnforcementRouter

__all__ = [
    "ActionClaimVerdict",
    "ActionLedgerHook",
    "ToolExecutionResolution",
    "ToolExecutionState",
    "get_tool_receipt",
    "recovery_decision",
    "resolve_tool_execution_state",
    "stable_execution_id",
    "tool_arguments_digest",
]


class ToolWorker(Worker):
    """Worker that executes tool calls from the messages channel.

    Extracts tool calls from the last assistant message, executes each through
    the shared ``ToolCallExecutor`` governance chain, captures evidence, and
    returns tool result messages in batch.
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
        super().__init__(event_store=event_store)
        # step7: the single-call governance chain lives in ToolCallExecutor;
        # this worker keeps batch orchestration only and delegates each call.
        self._executor = ToolCallExecutor(
            port=port,
            pre_tool_hook=pre_tool_hook,
            post_tool_hook=post_tool_hook,
            access_policy=access_policy,
            approval_callback=approval_callback,
            event_store=event_store,
            sandbox_enforcement=sandbox_enforcement,
            tool_rules=tool_rules,
            middleware_chains=middleware_chains,
            denial_tracker=denial_tracker,
            action_hook=action_hook,
        )

    async def _execute_single_tool(
        self,
        tool_call: dict,
        context: dict,
        execution_context: dict | None = None,
        node_id: str = "",
    ) -> dict[str, Any]:
        """Delegate the single-call governance chain to the shared executor."""
        return await self._executor.execute_call(tool_call, context, execution_context, node_id)

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

        outcomes = await asyncio.gather(
            *(self._execute_single_tool(tc, channel_snapshot, execution_context, node_id) for tc in tool_calls),
            return_exceptions=True,
        )
        # Drain already-started calls before propagating a terminal condition;
        # otherwise gather leaves effects running after the request has ended.
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome
        tool_results = [outcome for outcome in outcomes if isinstance(outcome, dict)]

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
