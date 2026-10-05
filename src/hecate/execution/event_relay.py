"""Outbox → platform read-model projection (step6 worker change).

The durable event log is the authoritative, transactionally-committed
governance record. The platform read model (``platform_events``) stays the
API/SSE query surface — this module is the single writer that feeds it from
the outbox, replacing the old practice of task-control emitting governance
events directly on the request chain (which broke the "critical state and
event in one transaction" rule whenever the two stores disagreed).

Projection rules:

- only platform-known event families are projected (mapping below); the
  action-ledger internals stay authoritative in the durable log and surface
  through the host's action-reconciliation API instead;
- the envelope's ``event_id`` deduplicates (the read model has a global
  unique index), so relay retries and replays never double-project;
- the workspace attribution is resolved from the durable task row's
  ``workspace_id`` (set by the platform at submit time);
- the stored sequence is re-allocated so one run's read stream stays
  monotonic across the governance and run-stream families.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import replace
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from hecate.contracts.execution.events import EventEnvelope
from hecate.execution.governance_events import (
    COMMAND_RECORDED,
    COMMAND_TRANSITIONED,
    RUN_TERMINAL,
    TASK_STATE_CHANGED,
    TASK_SUBMITTED,
    PlatformEventService,
)

logger = logging.getLogger(__name__)

# Durable outbox event_type → platform read-model payload schema ref.
DURABLE_EVENT_SCHEMA_MAP = {
    "task_submitted": TASK_SUBMITTED,
    "task_state": TASK_STATE_CHANGED,
    "command_recorded": COMMAND_RECORDED,
    "command_state": COMMAND_TRANSITIONED,
    "run_terminal": RUN_TERMINAL,
}


class PlatformOutboxProjector:
    """Async project callback handed to the durable worker's ``OutboxRelay``."""

    def __init__(
        self,
        store: Any,
        session_factory: async_sessionmaker,
    ) -> None:
        self._store = store
        self._session_factory = session_factory

    async def __call__(self, envelope_dict: dict[str, Any]) -> None:
        payload = envelope_dict.get("payload") or {}
        event_type = payload.get("event_type")
        schema_ref = DURABLE_EVENT_SCHEMA_MAP.get(str(event_type))
        if schema_ref is None:
            return  # not a platform read-model family; authoritative in the log
        envelope = EventEnvelope.from_dict(envelope_dict)
        workspace_id = await self._resolve_workspace(envelope.task_ref)
        async with self._session_factory() as db:
            events = PlatformEventService(db)
            if await events.find_by_event_id(envelope.event_id) is not None:
                return  # already projected (relay retry/replay)
            stored = replace(envelope, payload_schema_ref=schema_ref, received_at=stored_now())
            try:
                await events.append_resequenced(stored, workspace_id=workspace_id)
                if event_type == "run_terminal" and workspace_id is not None:
                    from hecate.execution.task_run_registry import TaskRunRegistry

                    await TaskRunRegistry(db).update_projection(
                        uuid.UUID(envelope.run_ref.id),
                        workspace_id,
                        projection={
                            "state": payload["status"],
                            "error": payload.get("error"),
                            "result_preview": str(payload.get("content") or "")[:2000],
                        },
                    )
                await db.commit()
            except IntegrityError:
                # Lost a dedup race with another relay instance — the event
                # is projected; nothing to do.
                await db.rollback()
                if await events.find_by_event_id(envelope.event_id) is None:
                    raise

    async def _resolve_workspace(self, task_ref: Any) -> uuid.UUID | None:
        """Task attribution: the row column, then the persisted input payload."""

        try:
            record = await asyncio.to_thread(self._store.get_task_state, task_ref)
        except Exception:  # noqa: BLE001 — attribution is best-effort; scoping stays safe
            return None
        if record is None:
            return None
        candidates = [(record.extra or {}).get("workspace_id")]
        try:
            payload = await asyncio.to_thread(self._store.get_task_input, task_ref) or {}
            candidates.append(payload.get("workspace_id"))
        except Exception:  # noqa: BLE001, S110 — attribution fallback only; the row column already covered it
            pass
        for raw in candidates:
            if raw:
                try:
                    return uuid.UUID(str(raw))
                except ValueError:
                    continue
        return None


def stored_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
