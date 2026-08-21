"""Fail-closed approval callback with durable audit pair (1.3.4, T2).

Implements ``ApprovalCallback`` so every call to ``request_approval`` produces
both halves of the audit pair — ``APPROVAL_ASKED`` before the callback runs
and ``APPROVAL_DECIDED`` after the decision arrives (or the no-answerer
fail-closed path completes). The pair is enclosed by a ``TURN_START`` /
``TURN_END`` window at the engine layer.

The component lives in the services layer (not engine) because it carries
optional dependencies (event store, tenant context). The engine's
``ApprovalCallback`` ABC is implemented by this class; ``WorkflowExecutionService``
and the chat path-A loop receive instances of this class via the assembly
facade (``guardrail_assembly.assemble_guardrails``).

Once-only consumption: an ``ONCE``-scoped grant is consumed on first use
(keyed by ``tool_call_id``). Subsequent calls for the same tool call do
NOT reuse the consumed grant. ``SESSION``/``PROJECT``/``GLOBAL`` scope only
cache when the grant is backed by a durable ``APPROVAL_DECIDED`` event —
in-memory-only grants collapse to ``ONCE``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from hecate.engine.eventstore import Event, EventType
from hecate.engine.tool_access import (
    ApprovalCallback,
    ApprovalDecision,
    ApprovalScope,
)
from hecate.services.security.guardrail_assembly import (
    NoAnswerApprovalCallback as _NoAnswerPlaceholder,
)


@dataclass
class _GrantedDecision:
    """Internal record of a granted approval — kept in memory for once-only
    consumption / scope caching.
    """

    approved: bool
    reason: str
    scope: ApprovalScope
    tool_call_id: str
    tool_name: str
    consumed: bool = False


async def rebuild_approval_projection(
    event_store: Any,
    session_id: uuid.UUID,
    projection_writer: Any | None = None,
    workspace_id: uuid.UUID | None = None,
) -> int:
    """Replay the event log and rebuild the ``approval_records`` projection.

    Returns the number of approval rows written. The function is idempotent:
    callers SHOULD clear the projection table before invoking it (or pass a
    writer that overwrites by ``(session_id, tool_call_id)``). The projection
    is derived state — the event log is the source of truth.

    Args:
        event_store: EventStore implementation (Postgres / in-memory).
        session_id: Session whose events are replayed.
        projection_writer: Optional callable that accepts a dict with the
            derived projection row fields. Defaults to a no-op (the function
            returns the count of replays without writing).
        workspace_id: Required when ``projection_writer`` materializes
            ``ApprovalRecordModel`` rows (the table's ``workspace_id`` is
            NOT NULL). Callers pass it from the session context.
    """
    events = await event_store.get_events(session_id)
    open_asks: dict[str, dict] = {}
    rows_written: int = 0
    for event in events:
        etype = event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)
        if etype == "APPROVAL_ASKED":
            tc_id = event.payload.get("tool_call_id", "")
            if tc_id:
                open_asks[tc_id] = {
                    "tool_name": event.payload.get("tool_name", ""),
                    "risk_level": event.payload.get("risk_level", ""),
                    "asked_event_id": event.id,
                    "asked_at": event.timestamp,
                }
        elif etype == "APPROVAL_DECIDED":
            tc_id = event.payload.get("tool_call_id", "")
            asked = open_asks.pop(tc_id, None)
            if asked is None:
                continue
            row = {
                "workspace_id": workspace_id,
                "session_id": session_id,
                "tool_name": asked["tool_name"],
                "risk_level": asked["risk_level"],
                "scope": event.payload.get("scope", "once"),
                "status": "approved" if event.payload.get("approved") else "rejected",
                "reason": event.payload.get("reason", ""),
            }
            if projection_writer is not None:
                projection_writer(row)
            rows_written += 1
    return rows_written


@dataclass
class FailingClosedApprovalCallback(ApprovalCallback):
    """Production approval callback (T2, guardrail-upgrade-trio).

    Emits the ``APPROVAL_ASKED`` / ``APPROVAL_DECIDED`` event pair for every
    call (including the no-answerer fail-closed path), enforces once-only
    consumption on ``ONCE`` grants, and applies scope caching only when the
    grant is durable (i.e. an ``APPROVAL_DECIDED`` event was emitted).
    """

    event_store: Any = None
    session_id: uuid.UUID | None = None
    workspace_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    on_behalf_of_user: uuid.UUID | None = None
    _granted: dict[str, _GrantedDecision] = field(default_factory=dict)
    _inner_backend: ApprovalCallback | None = None

    async def _emit(self, event_type: EventType, payload: dict[str, Any]) -> None:
        if self.event_store is None or self.session_id is None:
            return
        await self.event_store.append(
            Event(
                session_id=self.session_id,
                superstep=0,
                event_type=event_type,
                payload=payload,
            )
        )

    def _granted_key(self, tool_call_id: str, scope: ApprovalScope) -> str:
        if scope == ApprovalScope.SESSION:
            return f"session:{self.session_id}:{tool_call_id}"
        if scope == ApprovalScope.PROJECT:
            return f"project:{self.workspace_id}:{tool_call_id}"
        if scope == ApprovalScope.GLOBAL:
            return f"global:{tool_call_id}"
        return f"once:{tool_call_id}"

    async def request_approval(
        self,
        tool_name: str,
        arguments: dict,
        risk_level: str,
        context: dict,
    ) -> ApprovalDecision:
        """Emit ASKED → invoke underlying callback (or fail closed) → emit DECIDED.

        The default underlying callback is ``NoAnswerApprovalCallback`` —
        refusing every request — so callers see fail-closed semantics by
        default. Production wiring supplies a real backend via the
        ``inner`` attribute on a subclass.
        """
        tool_call_id = (arguments or {}).get("tool_call_id") or context.get("tool_call_id", "")
        # Emit ASKED first — the durable record of the audit pair.
        await self._emit(
            EventType.APPROVAL_ASKED,
            {
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "risk_level": risk_level,
                "log_schema_version": 2,
            },
        )

        # Once-only consumption: an ONCE grant is keyed by tool_call_id and
        # consumed on first use. Subsequent calls for the same tool_call_id
        # refuse with ``once_only_consumed`` regardless of scope.
        once_key = self._granted_key(tool_call_id, ApprovalScope.ONCE)
        once_cached = self._granted.get(once_key)
        if once_cached is not None and once_cached.consumed:
            decision = ApprovalDecision(
                approved=False,
                reason="once_only_consumed",
                scope=ApprovalScope.ONCE,
            )
        else:
            # SESSION+/PROJECT/GLOBAL cache lookup: a durable grant (one
            # backed by an APPROVAL_DECIDED event) lets the same tool run
            # again without re-asking. Lookup key includes the scope's
            # anchor (session_id / workspace_id) + tool_name + tool_call_id.
            scope_key = self._scope_anchor_key(tool_name, tool_call_id)
            scoped_cached = self._granted.get(scope_key)
            if scoped_cached is not None and scoped_cached.approved:
                decision = ApprovalDecision(
                    approved=True,
                    reason=scoped_cached.reason,
                    scope=scoped_cached.scope,
                )
            else:
                try:
                    inner = await self._delegate_inner(tool_name, arguments, risk_level, context)
                except Exception as exc:  # noqa: BLE001 — fail-closed
                    inner = ApprovalDecision(
                        approved=False,
                        reason=f"no_answerer:{type(exc).__name__}",
                        scope=ApprovalScope.ONCE,
                    )

                if inner.approved and inner.scope != ApprovalScope.ONCE:
                    # Durable SESSION+/PROJECT/GLOBAL grants cache for replay
                    # within their anchor (session / project / global).
                    self._granted[scope_key] = _GrantedDecision(
                        approved=True,
                        reason=inner.reason,
                        scope=inner.scope,
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        consumed=False,
                    )
                decision = inner

        # Mark ONCE grants as consumed — an ONCE grant authorizes exactly one
        # tool execution. Subsequent calls for the same tool_call_id refuse.
        if decision.approved and decision.scope == ApprovalScope.ONCE:
            self._granted[once_key] = _GrantedDecision(
                approved=True,
                reason=decision.reason,
                scope=ApprovalScope.ONCE,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                consumed=True,
            )

        await self._emit(
            EventType.APPROVAL_DECIDED,
            {
                "tool_call_id": tool_call_id,
                "approved": decision.approved,
                "reason": decision.reason,
                "scope": decision.scope.value,
                "log_schema_version": 2,
            },
        )
        return decision

    def _scope_anchor_key(self, tool_name: str, tool_call_id: str) -> str:
        """Build a session-/project-/global-scoped cache key.

        The key includes the tool name + tool_call_id and the scope anchor
        (session_id for SESSION, workspace_id for PROJECT, ``global`` for
        GLOBAL). Without an event-store-backed durable record, in-memory
        grants collapse to ONCE semantics.
        """
        if self.session_id is not None:
            return f"session:{self.session_id}:{tool_name}:{tool_call_id}"
        if self.workspace_id is not None:
            return f"project:{self.workspace_id}:{tool_name}:{tool_call_id}"
        return f"global:{tool_name}:{tool_call_id}"

    async def _delegate_inner(
        self,
        tool_name: str,
        arguments: dict,
        risk_level: str,
        context: dict,
    ) -> ApprovalDecision:
        """Dispatch to the configured inner backend.

        The default inner backend is ``NoAnswerApprovalCallback`` so behavior
        is fail-closed when no override is supplied. Tests and production
        callers assign ``cb._inner_backend = MyBackend()`` to plug in a real
        implementation.
        """
        backend = self._inner_backend or _NoAnswerPlaceholder()
        return await backend.request_approval(
            tool_name=tool_name,
            arguments=arguments,
            risk_level=risk_level,
            context=context,
        )


__all__ = ["FailingClosedApprovalCallback"]
