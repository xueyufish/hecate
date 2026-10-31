"""Persistent wake of expired platform controller timer waits.

A timer wait parks a controller task in ``waiting_input`` with
``wake_kind: "timer"`` and an absolute, receipt-anchored deadline. Unlike
input and approval waits, expiry is not a terminal: the wake is the
continuation — the waker consumes the wait under the same commit-time
re-verification the wake and cancel paths share (exactly one of {timer
wake, cancel, manual command} lands), requeues the task, and lets the
durable worker's next cycle re-dispatch it onto the SAME run, where the
accepted-decision chain replays the parked position.

The waker is the durable worker's named wait consumer, sharing the input
expirer's shape: one bounded, keyset-paged pass; only platform-locally
owned executions (shared ``verify_platform_local``); the wake deadline is
re-derived from the declaring action's durable receipt anchor on every
pass — a record whose stored deadline disagrees with its anchor, whose
contract is unreadable, or whose anchor is gone is parked for
reconciliation instead of guessed. No model call, no tool dispatch and no
input revision happen here: the wake only moves QUEUED, and the loop's
own budgets (turns, deadline, no-progress) keep their accumulated values.

Workflow child waits, approval waits, human input waits and foreign or
non-local executions are out of scope by construction.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from hecate_runtime.action_ledger import tool_arguments_digest

from hecate.contracts.execution.durable import InvalidTaskTransitionError, TaskLifecycleState
from hecate.contracts.execution.references import BackendRef, RefKind
from hecate.execution.approval_wait_expiration import verify_platform_local
from hecate.execution.timer_wait import (
    timer_deadline_for,
    timer_wait_contract,
    timer_wait_declaration,
    timer_wait_policy,
)

logger = logging.getLogger(__name__)

RECONCILIATION_CATEGORY = "timer_wait_unverifiable"


class TimerWaitWaker:
    """Bounded convergence pass that wakes due platform timer waits."""

    def __init__(
        self,
        store: Any,
        session_factory: Any,
        *,
        platform_issuer: str = "hecate",
        batch: int = 32,
        clock: Any | None = None,
    ) -> None:
        self._store = store
        self._session_factory = session_factory
        self._platform_issuer = platform_issuer
        if batch < 1:
            raise ValueError("timer wake batch must be positive")
        self._batch = batch
        self._clock = clock
        self._cursor: tuple[str, str, str] | None = None

    async def wake_once(self) -> int:
        """One bounded keyset pass; returns the number of woken waits."""

        records, self._cursor = await asyncio.to_thread(
            self._store.list_task_page,
            {TaskLifecycleState.WAITING_INPUT},
            after=self._cursor,
            limit=self._batch,
        )
        woken = 0
        for record in records:
            try:
                if await self._wake_one(record):
                    woken += 1
            except Exception:  # noqa: BLE001 — one bad record must not block the pass
                logger.warning("timer wake failed for task %s", record.task_ref.id, exc_info=True)
        return woken

    async def _wake_one(self, record: Any) -> bool:
        t_ref = record.task_ref
        if t_ref.issuer_domain != self._platform_issuer:
            # A foreign host owns the execution facts; the platform never
            # moves its tasks.
            return False
        wait = (record.extra or {}).get("wait")
        if not isinstance(wait, dict) or wait.get("wake_kind") != "timer":
            return False

        async with self._session_factory() as db:
            # Ownership gates every write, including corruption convergence.
            if not await verify_platform_local(db, self._store, t_ref, record):
                return False
            if (
                wait.get("consumed") is not False
                or not isinstance(wait.get("wait_token"), str)
                or not wait["wait_token"]
            ):
                return await self._park_for_reconciliation(t_ref, record, "timer wait token is missing or invalid")
            if wait.get("wait_expires_at") is None:
                return await self._park_for_reconciliation(t_ref, record, "timer wait deadline is missing")
            contract = timer_wait_contract(wait.get("contract_ref"))
            if contract is None:
                return await self._park_for_reconciliation(t_ref, record, "timer wait contract is unreadable")
            deadline = _parse(wait.get("wait_expires_at"))
            if deadline is None:
                return await self._park_for_reconciliation(t_ref, record, "timer wait deadline is malformed")
            run_ref = await asyncio.to_thread(self._store.run_for_task, t_ref)
            payload = await asyncio.to_thread(self._store.get_task_input, t_ref) or {}
            try:
                self._validate_policy(payload, record)
                await self._verify_receipt(t_ref, run_ref, contract)
            except (TypeError, ValueError, OverflowError) as exc:
                return await self._park_for_reconciliation(t_ref, record, str(exc))
            anchor_raw = await asyncio.to_thread(
                self._store.action_receipt_time,
                contract["execution_id"],
                task_ref=t_ref,
                run_ref=run_ref,
                action_name=contract["tool"],
                arguments_digest=contract["arguments_digest"],
            )
            anchor = _parse(anchor_raw)
            if anchor is None:
                return await self._park_for_reconciliation(t_ref, record, "timer wait anchor is unverifiable")
            # The deadline must still equal anchor + declared duration (with
            # the same task-deadline clamp used at park): recovery re-derives
            # it, it never restarts the clock and never accepts a moved one.
            try:
                expected = timer_deadline_for(anchor, float(contract["wake_seconds"]), _parse_deadline(payload))
            except (TypeError, ValueError, OverflowError):
                return await self._park_for_reconciliation(t_ref, record, "timer wait deadline binding is invalid")
            if datetime.fromisoformat(expected) != deadline:
                return await self._park_for_reconciliation(
                    t_ref, record, "timer wait deadline does not match its receipt anchor"
                )
            try:
                now = self._clock() if self._clock is not None else await asyncio.to_thread(self._store.wait_now)
                if not isinstance(now, datetime) or now.tzinfo is None:
                    raise ValueError("timer wait clock must be timezone-aware")
            except (TypeError, ValueError):
                return await self._park_for_reconciliation(t_ref, record, "timer wait clock cannot be trusted")
            if deadline > now:
                return False
            return await self._requeue(t_ref, record, wait, contract, now)

    @staticmethod
    def _validate_policy(payload: dict[str, Any], record: Any) -> None:
        controller = payload.get("controller")
        if payload.get("controller_loop") is not True or not isinstance(controller, dict) or payload.get("workflow"):
            raise ValueError("timer wait execution binding is invalid")
        policy = timer_wait_policy(controller.get("timer_wait"))
        count = (record.extra or {}).get("timer_waits")
        if policy is None or type(count) is not int or not 1 <= count <= policy["max_waits"]:
            raise ValueError("timer wait policy or count is invalid")
        acknowledged = payload.get("_input_wait_acknowledgements", [])
        if not isinstance(acknowledged, list) or any(not isinstance(item, str) for item in acknowledged):
            raise ValueError("timer wait acknowledgements are invalid")

    async def _verify_receipt(self, task_ref: BackendRef, run_ref: BackendRef | None, contract: dict[str, Any]) -> None:
        """Check the actual settled readonly receipt, not just a self-consistent deadline."""
        if run_ref is None or contract.get("run_id") != run_ref.id:
            raise ValueError("timer wait receipt belongs to another Run")
        row = await asyncio.to_thread(self._store.get_run_action, run_ref, contract["execution_id"])
        if not row:
            raise ValueError("timer wait receipt is missing")
        intent, outcome = row.get("intent") or {}, row.get("last_outcome") or {}
        if (
            (row.get("task_issuer"), row.get("task_id")) != (task_ref.issuer_domain, task_ref.id)
            or intent.get("side_effect_class") != "readonly"
            or intent.get("action_name") != contract["tool"]
            or intent.get("arguments_digest") != contract["arguments_digest"]
            or outcome.get("outcome") != "succeeded"
            or row.get("pending_reconciliation")
        ):
            raise ValueError("timer wait receipt identity or readonly outcome is invalid")
        result = row.get("result_payload")
        if tool_arguments_digest(result) != outcome.get("result_digest"):
            raise ValueError("timer wait receipt content digest mismatch")
        if isinstance(result, str):
            result = json.loads(result)
        declaration = timer_wait_declaration(result)
        if declaration is None or declaration["declaration"] != contract["declaration"]:
            raise ValueError("timer wait declaration does not match its action receipt")

    async def _requeue(
        self, t_ref: BackendRef, record: Any, wait: dict[str, Any], contract: dict[str, Any], now: datetime
    ) -> bool:
        """Consume the expired wait and requeue under the shared commit gate.

        ``wait_expired=True`` re-verifies the exact wait and its deadline
        under the row lock, so exactly one of {timer wake, cancel, manual
        command} can land; a loser keeps the winner's fact. The settled
        declaring action is acknowledged in the task input, so the wake's
        replay never re-parks on it.
        """

        payload = await asyncio.to_thread(self._store.get_task_input, t_ref) or {}
        acknowledged = list(payload.get("_input_wait_acknowledgements", []))
        execution_id = str(contract["execution_id"])
        if execution_id not in acknowledged:
            acknowledged.append(execution_id)
        try:
            await asyncio.to_thread(
                self._store.apply_task_state,
                t_ref,
                TaskLifecycleState.QUEUED,
                expected_revision=record.revision,
                expected_wait=wait,
                event_run_ref=BackendRef(RefKind.RUN, t_ref.issuer_domain, str(contract["run_id"])),
                expected_wait_state=TaskLifecycleState.WAITING_INPUT,
                wait_expired=True,
                wait_clock=self._clock,
                extra_update={
                    "wait": {**wait, "consumed": True},
                    "timer_wake": {"woke_at": now.isoformat(), "execution_id": execution_id},
                },
                input_payload={**payload, "_input_wait_acknowledgements": acknowledged},
            )
        except (InvalidTaskTransitionError, ValueError):
            # A cancel, a terminal convergence or another scan moved the
            # task first; their fact stands and this scan claims nothing.
            logger.info("task %s timer wake skipped (moved concurrently)", t_ref.id)
            return False
        logger.info("task %s timer wake consumed; requeued", t_ref.id)
        return True

    async def _park_for_reconciliation(self, t_ref: BackendRef, record: Any, reason: str) -> bool:
        outcome = {
            "status": "awaiting_reconciliation",
            "content": "",
            "error": reason,
            "error_category": RECONCILIATION_CATEGORY,
        }
        try:
            await asyncio.to_thread(
                self._store.apply_task_state,
                t_ref,
                TaskLifecycleState.RECONCILIATION_REQUIRED,
                expected_revision=record.revision,
                extra_update={"reconciliation_reason": reason, "result": outcome},
            )
        except (InvalidTaskTransitionError, ValueError):
            return False
        logger.warning("task %s parked for reconciliation: %s", t_ref.id, reason)
        return True


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _parse_deadline(payload: dict[str, Any]) -> datetime | None:
    """The task's own controller deadline, as declared at submission."""

    controller = payload.get("controller") if isinstance(payload.get("controller"), dict) else {}
    raw = controller.get("deadline")
    if raw is None:
        return None
    deadline = _parse(raw)
    if deadline is None:
        raise ValueError("task deadline is malformed")
    return deadline
