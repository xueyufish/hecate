"""Optional runtime bridge over the durable action ledger and decisions.

Import requires the runtime extra; the core store and wire contracts remain
independent of any runtime implementation. The runner and platform share
this hook, while other backends consume the ledger through their adapters.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from hecate_runtime.accepted_decision import (
    AcceptedDecisionRecord,
    AcceptedDecisionStore,
    DecisionConflictError,
    DecisionOwnership,
    DecisionStoreUnavailableError,
    DecisionValidationError,
    RunRef,
    StaleDecisionFenceError,
    canonical_decision_digest,
    snapshot_decision,
)
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

    async def validate_accepted_dispatch(
        self, *, run_ref: RunRef, ownership: DecisionOwnership | None, execution_id: str
    ) -> None:
        """Bind accepted actions to this hook's Task, Run and actual lease."""
        await asyncio.to_thread(self._validate_accepted_sync, run_ref, ownership, execution_id)

    def _validate_accepted_sync(self, run_ref: RunRef, ownership: DecisionOwnership | None, execution_id: str) -> None:
        from sqlalchemy import select

        from .contracts.references import RefKind
        from .storage.models import ActionIntentRow, TaskStateRow

        if (
            self._run_ref.kind != RefKind.RUN
            or self._task_ref.kind != RefKind.TASK
            or self._run_ref.issuer_domain != run_ref.issuer_domain
            or self._run_ref.id != run_ref.run_id
        ):
            raise DecisionValidationError("action ledger is bound to another Run")
        if (
            ownership is None
            or self._lease is None
            or self._holder != ownership.holder
            or self._lease.lease_key != ownership.lease_key
            or self._lease.holder != ownership.holder
            or self._lease.fencing_token != ownership.fence_token
        ):
            raise StaleDecisionFenceError("accepted-action", ownership.fence_token if ownership else -1, -1)
        with self._store.session_factory() as session, session.begin():
            SqlAcceptedDecisionStore(self._store)._assert_owner(session, run_ref, "accepted-action", ownership)
            task = session.execute(
                select(TaskStateRow).where(
                    TaskStateRow.task_issuer == self._task_ref.issuer_domain,
                    TaskStateRow.task_id == self._task_ref.id,
                    TaskStateRow.run_issuer == run_ref.issuer_domain,
                    TaskStateRow.run_id == run_ref.run_id,
                )
            ).scalar_one_or_none()
            if task is None:
                raise DecisionValidationError("action ledger Task does not own the requested Run")
            row = session.get(ActionIntentRow, execution_id)
            if row is not None and (
                row.task_issuer != self._task_ref.issuer_domain
                or row.task_id != self._task_ref.id
                or row.run_issuer != run_ref.issuer_domain
                or row.run_id != run_ref.run_id
            ):
                raise DecisionValidationError("recorded action belongs to another Task or Run")

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
            payload = self._store.action_result(execution_id, self._task_ref)
            content = _stringify(payload) if payload is not None else None
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


class SqlAcceptedDecisionStore(AcceptedDecisionStore):
    """Immutable accepted decisions over the existing durable transaction/outbox."""

    def __init__(self, store: SqlDurableStore) -> None:
        self._store = store

    async def lookup(self, run_ref: RunRef, decision_step_id: str) -> AcceptedDecisionRecord | None:
        try:
            return await asyncio.to_thread(self._lookup_sync, run_ref, decision_step_id)
        except Exception as exc:
            raise DecisionStoreUnavailableError("decision lookup failed or persisted content is invalid") from exc

    async def commit(
        self, record: AcceptedDecisionRecord, ownership: DecisionOwnership | None = None
    ) -> AcceptedDecisionRecord:
        # Snapshot before yielding to the worker thread so later caller mutation
        # cannot change the payload after its digest was calculated.
        record = snapshot_decision(record)
        try:
            return await asyncio.to_thread(self._commit_sync, record, ownership)
        except (DecisionConflictError, StaleDecisionFenceError, DecisionValidationError):
            raise
        except Exception as exc:
            raise DecisionStoreUnavailableError("decision commit failed") from exc

    def validate_ownership(self, run_ref: RunRef, ownership: DecisionOwnership | None) -> None:
        """Use the same authoritative lease validator for the contract double."""
        with self._store.session_factory() as session, session.begin():
            self._assert_owner(session, run_ref, "ownership-check", ownership)

    def _assert_owner(self, session, run_ref: RunRef, step: str, ownership: DecisionOwnership | None) -> None:
        from sqlalchemy import select

        from .storage.lease import LeaseHandle, StaleFenceError, task_dispatch_lease_key
        from .storage.models import TaskStateRow

        task = session.execute(
            select(TaskStateRow).where(
                TaskStateRow.run_issuer == run_ref.issuer_domain,
                TaskStateRow.run_id == run_ref.run_id,
            )
        ).scalar_one_or_none()
        if task is None:
            raise DecisionValidationError("decision Run must have a durable Task association")

        if (
            ownership is None
            or ownership.lease_key != task_dispatch_lease_key(task.task_issuer, task.task_id)
            or not isinstance(ownership.holder, str)
            or not ownership.holder.strip()
            or type(ownership.fence_token) is not int
            or ownership.fence_token < 1
        ):
            raise StaleDecisionFenceError(step, ownership.fence_token if ownership else -1, -1)
        try:
            # LeaseManager locks the authoritative row and checks holder, exact
            # generation and DB expiry. The decision row is never a lease authority.
            self._store.leases.assert_valid(
                session, LeaseHandle(ownership.lease_key, ownership.holder, ownership.fence_token, "")
            )
        except StaleFenceError as exc:
            raise StaleDecisionFenceError(step, exc.presented_token, exc.current_token) from exc

    def _lookup_sync(self, run_ref: RunRef, decision_step_id: str) -> AcceptedDecisionRecord | None:
        from sqlalchemy import select

        from .storage.models import AcceptedDecisionRow

        with self._store.session_factory() as session:
            row = session.execute(
                select(AcceptedDecisionRow).where(
                    AcceptedDecisionRow.issuer_domain == run_ref.issuer_domain,
                    AcceptedDecisionRow.run_id == run_ref.run_id,
                    AcceptedDecisionRow.decision_step_id == decision_step_id,
                )
            ).scalar_one_or_none()
            return None if row is None else self._row_to_record(row)

    def _commit_sync(
        self, record: AcceptedDecisionRecord, ownership: DecisionOwnership | None
    ) -> AcceptedDecisionRecord:
        import uuid
        from datetime import UTC, datetime

        from sqlalchemy import select
        from sqlalchemy.exc import IntegrityError

        from .contracts.references import RefKind
        from .storage.models import AcceptedDecisionRow, TaskStateRow

        digest = canonical_decision_digest(record)
        run_ref = record.run_ref
        for attempt in range(3):
            try:
                with self._store.session_factory() as session, session.begin():
                    self._assert_owner(session, run_ref, record.decision_step_id, ownership)
                    existing = session.execute(
                        select(AcceptedDecisionRow).where(
                            AcceptedDecisionRow.issuer_domain == run_ref.issuer_domain,
                            AcceptedDecisionRow.run_id == run_ref.run_id,
                            AcceptedDecisionRow.decision_step_id == record.decision_step_id,
                        )
                    ).scalar_one_or_none()
                    if existing is not None:
                        stored = self._row_to_record(existing)
                        if stored.content_digest != digest:
                            raise DecisionConflictError(record.decision_step_id, stored.content_digest or "", digest)
                        return stored

                    # Derive the task from durable authority, never fabricate a task
                    # reference or accept an unrelated caller-supplied association.
                    task = session.execute(
                        select(TaskStateRow).where(
                            TaskStateRow.run_issuer == run_ref.issuer_domain,
                            TaskStateRow.run_id == run_ref.run_id,
                        )
                    ).scalar_one_or_none()
                    if task is None:
                        raise DecisionValidationError("decision Run must have a durable Task association")
                    if ownership is None:
                        raise StaleDecisionFenceError(record.decision_step_id, -1, -1)
                    row = AcceptedDecisionRow(
                        id=uuid.uuid4().hex,
                        issuer_domain=run_ref.issuer_domain,
                        run_id=run_ref.run_id,
                        decision_step_id=record.decision_step_id,
                        schema_version=record.schema_version,
                        input_refs=record.input_refs,
                        accepted_output=record.accepted_output,
                        actions=[action.to_dict() for action in record.actions],
                        content_digest=digest,
                        fence_holder=ownership.holder,
                        fence_token=ownership.fence_token,
                        accepted_at=datetime.now(UTC).isoformat(),
                    )
                    session.add(row)
                    session.flush()
                    self._store.events.emit(
                        session,
                        task_ref=BackendRef(RefKind.TASK, task.task_issuer, task.task_id),
                        run_ref=BackendRef(RefKind.RUN, run_ref.issuer_domain, run_ref.run_id),
                        event_type="agent_decision_accepted",
                        payload={
                            "decision_step_id": record.decision_step_id,
                            "content_digest": digest,
                            "schema_version": record.schema_version,
                            "action_keys": [action.action_key for action in record.actions],
                        },
                    )
                    return self._row_to_record(row)
            except IntegrityError:
                # A racing insert is arbitrated in a fresh transaction, including
                # a fresh ownership check. Non-identity constraints must not become
                # false idempotent success; exhausted retries fail as storage errors.
                if attempt == 2:
                    raise
        raise RuntimeError("decision commit did not converge")  # pragma: no cover

    @staticmethod
    def _row_to_record(row: Any) -> AcceptedDecisionRecord:
        record = AcceptedDecisionRecord.from_dict(
            {
                "schema_version": row.schema_version,
                "run_ref": {"issuer_domain": row.issuer_domain, "run_id": row.run_id},
                "decision_step_id": row.decision_step_id,
                "input_refs": row.input_refs,
                "accepted_output": row.accepted_output,
                "actions": row.actions,
                "accepted_at": row.accepted_at,
                "content_digest": row.content_digest,
            }
        )
        if not record.accepted_at or record.content_digest != canonical_decision_digest(record):
            raise DecisionStoreUnavailableError("persisted accepted decision is corrupt")
        return record


class InMemoryAcceptedDecisionStore(AcceptedDecisionStore):
    """Detached process-local double, with host-injected authoritative fencing.

    The validator is a synchronous host capability, not another lease registry.
    An absent validator rejects writes; this double grants no restart guarantee.
    """

    def __init__(self, *, validate_ownership=None, unavailable: bool = False, fail_commits: bool = False) -> None:
        import threading

        self._records: dict[tuple[str, str, str], AcceptedDecisionRecord] = {}
        self._validate_ownership = validate_ownership
        self._unavailable = unavailable
        self._fail_commits = fail_commits
        self._lock = threading.RLock()

    async def lookup(self, run_ref: RunRef, decision_step_id: str) -> AcceptedDecisionRecord | None:
        with self._lock:
            if self._unavailable:
                raise DecisionStoreUnavailableError("decision store unavailable")
            record = self._records.get((run_ref.issuer_domain, run_ref.run_id, decision_step_id))
            return None if record is None else snapshot_decision(record)

    async def commit(
        self, record: AcceptedDecisionRecord, ownership: DecisionOwnership | None = None
    ) -> AcceptedDecisionRecord:
        from dataclasses import replace
        from datetime import UTC, datetime

        record = snapshot_decision(record)
        with self._lock:
            if self._unavailable or self._fail_commits:
                raise DecisionStoreUnavailableError("decision store unavailable")
            if self._validate_ownership is None:
                raise StaleDecisionFenceError(record.decision_step_id, -1, -1)
            self._validate_ownership(record.run_ref, ownership)
            digest = canonical_decision_digest(record)
            key = (record.run_ref.issuer_domain, record.run_ref.run_id, record.decision_step_id)
            existing = self._records.get(key)
            if existing is not None:
                if existing.content_digest != digest:
                    raise DecisionConflictError(record.decision_step_id, existing.content_digest or "", digest)
                return snapshot_decision(existing)
            stored = replace(record, accepted_at=datetime.now(UTC).isoformat(), content_digest=digest)
            self._records[key] = stored
            return snapshot_decision(stored)
