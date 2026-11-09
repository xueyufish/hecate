"""Read one REST submitter's result inbox from committed platform events."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from hecate.contracts.execution.durable import TaskStateRecord
from hecate.contracts.execution.events import EventEnvelope
from hecate.contracts.execution.references import BackendRef, RefKind
from hecate.execution.governance_events import RUN_TERMINAL, TASK_STATE_CHANGED, PlatformEventService
from hecate.execution.task_control import (
    RESULT_DELIVERY_REST_INBOX,
    TaskControlError,
    TaskControlNotFoundError,
    TaskControlService,
)
from hecate.execution.task_input import plain_human_input_wait
from hecate.models.run import RunModel

_CURSOR_VERSION = "v1"
_SCAN_WINDOW = 200
_STOP_STATES = frozenset({"failed", "cancelled"})


class ResultInboxCursorError(ValueError):
    """A malformed cursor or a cursor belonging to another run."""


class TaskResultInboxService:
    """Read a bounded notification page for the original submitter and Run."""

    def __init__(self, control: TaskControlService) -> None:
        self._control = control

    async def read_page(
        self,
        *,
        workspace_id: uuid.UUID,
        task_id: uuid.UUID,
        user_id: uuid.UUID,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Authorize the source, then scan one bounded window of stored events."""
        control = self._control
        task = await control._get_task(workspace_id, task_id)
        unavailable = TaskControlNotFoundError("result inbox not found")
        if not isinstance(task.initiator_ref, dict) or task.initiator_ref.get("initiator") != str(user_id):
            raise unavailable
        t_ref = BackendRef(RefKind.TASK, task.issuer_domain, str(task.id))
        task_input = await asyncio.to_thread(control._store.get_task_input, t_ref) or {}
        binding = task_input.get("result_delivery")
        if (
            not isinstance(binding, dict)
            or binding.get("mode") != RESULT_DELIVERY_REST_INBOX
            or binding.get("workspace_id") != str(workspace_id)
            or binding.get("submitted_by") != str(user_id)
        ):
            raise unavailable
        try:
            run_id = uuid.UUID(str(task_input.get("platform_run_id")))
        except ValueError:
            raise unavailable from None
        run = await control.assert_run_observable(workspace_id=workspace_id, run_id=run_id)
        if run.task_id != task.id:
            raise unavailable
        r_ref = BackendRef(RefKind.RUN, run.issuer_domain, str(run.id))
        sequence = self._parse_cursor(cursor, run_id) if cursor is not None else None
        page = await PlatformEventService(control._db).read_run_events(
            r_ref, workspace_id=workspace_id, cursor=sequence, limit=_SCAN_WINDOW
        )

        notifications: list[dict[str, Any]] = []
        next_cursor = cursor
        has_more = page.has_more
        if page.events:
            record = await asyncio.to_thread(control._store.get_task_state, t_ref)
            if record is None:
                raise TaskControlError("result inbox task state is unavailable")
            seen: set[str] = set()
            for index, envelope in enumerate(page.events):
                if envelope.task_ref != t_ref or envelope.run_ref != r_ref:
                    raise TaskControlError("result inbox event binding is invalid")
                note = await self._notification_for(envelope, task_id, run, record, task_input)
                next_cursor = f"{_CURSOR_VERSION}:{run_id}:{envelope.source_sequence}"
                if note is not None and note["notification_id"] not in seen:
                    seen.add(note["notification_id"])
                    notifications.append(note)
                    if len(notifications) == limit:
                        has_more = page.has_more or index + 1 < len(page.events)
                        break

        return {
            "task_id": str(task_id),
            "result_delivery": RESULT_DELIVERY_REST_INBOX,
            "notifications": notifications,
            "next_cursor": next_cursor,
            "has_more": has_more,
        }

    async def _notification_for(
        self,
        envelope: EventEnvelope,
        task_id: uuid.UUID,
        run: RunModel,
        record: TaskStateRecord,
        task_input: dict[str, Any],
    ) -> dict[str, Any] | None:
        payload = envelope.payload or {}
        lifecycle = record.lifecycle_state.value
        task_path = f"/api/tasks/{task_id}"
        views = {"task": task_path}
        if envelope.payload_schema_ref == RUN_TERMINAL:
            # The outbox commits the terminal envelope and delivery projection
            # together. Task success alone cannot announce a readable result.
            if lifecycle != "succeeded" or payload.get("status") != "succeeded":
                return None
            await self._control._db.refresh(run, ["projection"])
            projection = run.projection or {}
            if projection.get("state") != "succeeded" or not isinstance(projection.get("result_preview"), str):
                raise TaskControlError("result inbox delivery projection is unavailable")
            return self._completion_note(envelope, task_id, run.id, lifecycle)
        if envelope.payload_schema_ref != TASK_STATE_CHANGED:
            return None
        state = payload.get("state")
        revision = payload.get("revision")
        is_current = state == lifecycle and type(revision) is int and revision == record.revision
        if state in _STOP_STATES and is_current:
            return self._completion_note(envelope, task_id, run.id, lifecycle)
        if state == "reconciliation_required":
            return self._wait_note(
                envelope,
                task_id,
                run.id,
                "reconciliation_required",
                is_current,
                {**views, "reconciliation": "/api/reconciliation/pending"},
            )
        wait = _current_wait(record) if is_current else None
        if state == "waiting_input":
            if (
                wait is None
                or wait.get("wake_kind") != "provide_input"
                or not plain_human_input_wait(wait.get("contract_ref"))
            ):
                return None
            return self._wait_note(
                envelope,
                task_id,
                run.id,
                "input_required",
                True,
                {**views, "commands": f"{task_path}/commands"},
            )
        if state == "waiting_approval":
            pending = wait is not None and await self._approval_pending(wait, task_input, task_id)
            return self._wait_note(
                envelope,
                task_id,
                run.id,
                "approval_required",
                pending,
                {**views, "approval": f"{task_path}/approval"},
            )
        return None

    async def _approval_pending(self, wait: dict[str, Any], task_input: dict[str, Any], task_id: uuid.UUID) -> bool:
        from hecate.enterprise.governance_ledgers import SqlApprovalLedger
        from hecate.execution.approval_decision import ApprovalDecisionError, ApprovalDecisionService

        contract = wait.get("contract_ref")
        if wait.get("wake_kind") != "resume" or not isinstance(contract, dict):
            return False
        approval_ref = contract.get("approval_ref")
        if not isinstance(approval_ref, str) or not approval_ref:
            return False
        binding = await SqlApprovalLedger(self._control._db).get(approval_ref)
        try:
            ApprovalDecisionService._validate_binding(binding, wait, task_input, task_id, trusted=False)
        except ApprovalDecisionError:
            return False
        return True

    @staticmethod
    def _completion_note(envelope: EventEnvelope, task_id: uuid.UUID, run_id: uuid.UUID, state: str) -> dict[str, Any]:
        succeeded = state == "succeeded"
        return {
            "notification_id": f"completion:{task_id}",
            "type": "result_ready" if succeeded else "task_stopped",
            "category": "result" if succeeded else "stop",
            "task_id": str(task_id),
            "run_id": str(run_id),
            "state": state,
            "occurred_at": envelope.occurred_at,
            "views": {"task": f"/api/tasks/{task_id}", "run_detail": f"/api/runs/{run_id}"},
        }

    @staticmethod
    def _wait_note(
        envelope: EventEnvelope,
        task_id: uuid.UUID,
        run_id: uuid.UUID,
        notification_type: str,
        open_now: bool,
        views: dict[str, str],
    ) -> dict[str, Any]:
        prefix = {
            "approval_required": "approval",
            "input_required": "input",
            "reconciliation_required": "reconciliation",
        }[notification_type]
        return {
            "notification_id": f"{prefix}:{envelope.event_id}",
            "type": notification_type,
            "category": "needs_attention",
            "task_id": str(task_id),
            "run_id": str(run_id),
            "occurred_at": envelope.occurred_at,
            "status": "open" if open_now else "resolved",
            "views": views,
        }

    @staticmethod
    def _parse_cursor(cursor: str, run_id: uuid.UUID) -> str:
        parts = cursor.split(":")
        if len(parts) != 3 or parts[0] != _CURSOR_VERSION or parts[1] != str(run_id):
            raise ResultInboxCursorError("cursor is not valid for this result inbox")
        sequence = parts[2]
        if not sequence.isascii() or not sequence.isdecimal() or len(sequence) > 10 or int(sequence) >= 2**31:
            raise ResultInboxCursorError("cursor is not valid for this result inbox")
        return sequence


def _current_wait(record: TaskStateRecord) -> dict[str, Any] | None:
    wait = (record.extra or {}).get("wait")
    if not isinstance(wait, dict) or wait.get("consumed") is not False or not wait.get("wait_token"):
        return None
    deadline = wait.get("wait_expires_at")
    if deadline is not None:
        if not isinstance(deadline, str):
            return None
        try:
            expiry = datetime.fromisoformat(deadline)
        except ValueError:
            return None
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        if expiry <= datetime.now(UTC):
            return None
    return wait
