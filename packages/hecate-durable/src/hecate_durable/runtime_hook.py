"""Optional runtime bridge over the durable action ledger.

Import requires the runtime extra; the core store and wire contracts remain
independent of any runtime implementation. The runner and platform share
this hook, while other backends consume the ledger through their adapters.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from hecate_runtime.action_ledger import (
    ActionClaimVerdict,
    ActionLedgerHook,
    ToolExecutionResolution,
    ToolExecutionState,
    tool_arguments_digest,
)

from hecate_durable.contracts.durable import ActionIntent, ActionOutcome, ActionOutcomeRecord, IdempotencyConflictError
from hecate_durable.contracts.references import BackendRef
from hecate_durable.contracts.tools import ToolSideEffectClass
from hecate_durable.storage import SqlDurableStore

logger = logging.getLogger(__name__)


class SqlActionLedgerHook(ActionLedgerHook):
    """Kernel hook over the persistent ledger for one run.

    One instance per run: it carries the run/task correlation that intent
    rows persist alongside the action key, so platform Actions map onto the
    runtime's TOOL_CALL/TOOL_RESULT identity without fabricating events.
    """

    def __init__(
        self, store: SqlDurableStore, *, task_ref: BackendRef, run_ref: BackendRef, holder: str = "host", lease=None
    ) -> None:
        self._store = store
        self._holder = holder
        self._lease = lease
        self._task_ref = task_ref
        self._run_ref = run_ref
        self._claim_tokens: dict[str, int] = {}
        self._failed_outcomes: set[str] = set()
        self._lock = asyncio.Lock()

    @property
    def has_failed_outcomes(self) -> bool:
        """Whether any outcome write failed (run stays pending reconciliation)."""

        return bool(self._failed_outcomes)

    async def resolve(self, *, session_id: str, execution_id: str) -> ToolExecutionResolution:
        try:
            return await asyncio.to_thread(self._resolve_sync, execution_id)
        except Exception:
            logger.warning("Ledger resolve failed for %s", execution_id, exc_info=True)
            return ToolExecutionResolution(state=ToolExecutionState.STORE_UNAVAILABLE)

    def _resolve_sync(self, execution_id: str) -> ToolExecutionResolution:
        recovery = self._store.recovery(execution_id)
        return self._to_resolution(execution_id, recovery)

    async def record_claim(
        self,
        *,
        session_id: str,
        execution_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict,
        arguments_digest: str,
        side_effect_class: str,
    ) -> ActionClaimVerdict:
        intent = ActionIntent(
            action_key=execution_id,
            action_name=tool_name,
            arguments_digest=arguments_digest,
            side_effect_class=ToolSideEffectClass(side_effect_class),
        )
        try:
            async with self._lock:
                await asyncio.to_thread(
                    self._store.record_intent_ex,
                    intent,
                    task_ref=self._task_ref,
                    run_ref=self._run_ref,
                    session_id=session_id,
                    execution_id=execution_id,
                    tool_call_id=tool_call_id,
                    lease=self._lease,
                )
                receipt, token = await asyncio.to_thread(
                    self._store.claim_ex, execution_id, holder=self._holder, lease=self._lease
                )
        except Exception as exc:
            if isinstance(exc, IdempotencyConflictError):
                return ActionClaimVerdict(
                    claimed=False,
                    conflict=(
                        "[conflict] arguments differ from the recorded execution for this action; execution withheld"
                    ),
                )
            logger.warning("Ledger claim failed for %s", execution_id, exc_info=True)
            raise
        if not receipt.claimed:
            resolution = self._to_resolution(execution_id, receipt.recovery)
            return ActionClaimVerdict(claimed=False, resolution=resolution)
        if token is not None:
            self._claim_tokens[execution_id] = token
        return ActionClaimVerdict(claimed=True)

    async def record_outcome(
        self,
        *,
        session_id: str,
        execution_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments_digest: str,
        status: str,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        outcome = ActionOutcome.UNKNOWN if status == "unknown" else ActionOutcome(status)
        record = ActionOutcomeRecord(
            action_key=execution_id,
            outcome=outcome,
            result_digest=self._result_digest(result, error),
        )
        token = self._claim_tokens.get(execution_id)
        try:
            async with self._lock:
                await asyncio.to_thread(
                    self._store.record_outcome_ex,
                    record,
                    claim_token=token,
                    result_payload=self._result_payload(result, error),
                )
        except Exception:
            # The side effect already happened; keep the action claimed so
            # recovery fails closed instead of re-running it.
            self._failed_outcomes.add(execution_id)
            logger.warning(
                "Ledger outcome write failed for %s — stays claimed, pending reconciliation",
                execution_id,
                exc_info=True,
            )
            raise

    @staticmethod
    def _result_digest(result: Any, error: str | None) -> str | None:
        if error is not None:
            return tool_arguments_digest({"error": error[:500]})
        if result is None:
            return None
        return tool_arguments_digest(result)

    @staticmethod
    def _result_payload(result: Any, error: str | None) -> Any:
        if error is not None:
            return {"error": error[:500]}
        return result

    def _to_resolution(self, execution_id: str, recovery) -> ToolExecutionResolution:
        from hecate_durable.contracts.durable import ActionLedgerState

        state = recovery.state
        last = recovery.last_outcome
        if state is ActionLedgerState.STORE_UNAVAILABLE:
            return ToolExecutionResolution(state=ToolExecutionState.STORE_UNAVAILABLE)
        if state is ActionLedgerState.NEVER_STARTED:
            return ToolExecutionResolution(state=ToolExecutionState.NEVER_STARTED)
        if state is ActionLedgerState.OUTCOME_UNKNOWN:
            return ToolExecutionResolution(
                state=ToolExecutionState.OUTCOME_UNKNOWN,
                arguments_digest=recovery.intent.arguments_digest if recovery.intent else None,
                tool_name=recovery.intent.action_name if recovery.intent else None,
            )
        if last is not None and last.outcome is ActionOutcome.SUCCEEDED:
            # The real recorded result content is the cross-restart backfill.
            content = None
            for action in self._store.list_run_actions(self._run_ref):
                if action.get("action_key") == execution_id and action.get("result_payload") is not None:
                    content = _stringify(action["result_payload"])
                    break
            return ToolExecutionResolution(
                state=ToolExecutionState.SUCCEEDED,
                arguments_digest=recovery.intent.arguments_digest if recovery.intent else None,
                result_digest=last.result_digest,
                tool_name=recovery.intent.action_name if recovery.intent else None,
                result_content=content,
            )
        if last is not None and last.outcome is ActionOutcome.FAILED:
            return ToolExecutionResolution(
                state=ToolExecutionState.FAILED,
                arguments_digest=recovery.intent.arguments_digest if recovery.intent else None,
                result_digest=last.result_digest,
                tool_name=recovery.intent.action_name if recovery.intent else None,
            )
        return ToolExecutionResolution(
            state=ToolExecutionState.CLAIMED,
            arguments_digest=recovery.intent.arguments_digest if recovery.intent else None,
            tool_name=recovery.intent.action_name if recovery.intent else None,
        )


def _stringify(payload: Any) -> str:
    import json

    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
