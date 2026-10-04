"""Tool-execution identity, recovery states, and the durable action-ledger hook.

This module owns the vocabulary every recovery decision shares:

- ``ToolExecutionState`` — the G2 four-state recovery vocabulary plus the
  ``succeeded``/``failed`` terminals;
- ``ToolExecutionResolution`` / ``tool_arguments_digest`` /
  ``resolve_tool_execution_state`` — resolving an execution's state from the
  runtime event log;
- ``recovery_decision`` — the single decision table mapping a resolution to
  "execute" or a withheld/blocked terminal result. The event-store path and
  the persistent-ledger path (``ActionLedgerHook``) both consume this one
  function so the two authorities can never drift;
- ``ActionLedgerHook`` — the optional kernel extension point a host or
  platform implements to mirror tool executions into a persistent action
  ledger (step6 ``durable-execution-core``; named consumer: the standalone
  runner's adapter). The hook carries the correlation identity
  (session/execution/tool_call) that links a platform Action to the runtime's
  ``TOOL_CALL``/``TOOL_RESULT`` events without fabricating Pregel events.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from hecate_runtime.eventstore import EventType
from hecate_runtime.tool_side_effects import (
    RECEIPT_FAILED,
    RECEIPT_SUCCEEDED,
    RECEIPT_UNKNOWN,
    SideEffectClass,
    should_auto_retry,
)

logger = logging.getLogger(__name__)


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
    tool_name: str | None = None
    # Real recorded result content (persistent-ledger source). Channel
    # history remains the first backfill source; this is the cross-restart
    # one. Never fabricated — empty unless a ledger actually recorded it.
    result_content: str | None = None


def tool_arguments_digest(arguments: Any) -> str:
    """Stable digest of tool arguments for action-key conflict detection.

    Canonical JSON (sorted keys, compact separators) so equivalent payloads
    hash identically regardless of key order. Invalid JSON values raise
    rather than colliding with an empty argument object.
    """
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def resolve_tool_execution_state(event_store: Any, session_id: Any, execution_id: str) -> ToolExecutionResolution:
    """Resolve the recovery state of a tool execution from the event log.

    Consumes both ``TOOL_CALL`` (the claim) and ``TOOL_RESULT`` (the
    receipt) so recovery decisions rest on recorded state instead of the
    absence of records. Each new claim supersedes the preceding attempt's
    receipt. Legacy claim arguments can still establish execution identity.
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
            receipt = None
        elif event.event_type is EventType.TOOL_RESULT:
            receipt = payload
    identity = receipt or claim or {}
    arguments_digest = identity.get("arguments_digest") or (claim or {}).get("arguments_digest")
    if arguments_digest is None and claim is not None and isinstance(claim.get("arguments"), dict):
        try:
            arguments_digest = tool_arguments_digest(claim["arguments"])
        except (TypeError, ValueError):
            arguments_digest = None
    recorded_name = identity.get("tool_name") or (claim or {}).get("tool_name")
    if receipt is not None:
        status = receipt.get("status")
        if status == RECEIPT_SUCCEEDED:
            state = ToolExecutionState.SUCCEEDED
        elif status == RECEIPT_UNKNOWN:
            state = ToolExecutionState.OUTCOME_UNKNOWN
        elif status == RECEIPT_FAILED:
            state = ToolExecutionState.FAILED
        else:
            state = ToolExecutionState.OUTCOME_UNKNOWN
        return ToolExecutionResolution(
            state=state,
            arguments_digest=arguments_digest,
            result_digest=receipt.get("result_digest"),
            tool_name=recorded_name,
        )
    if claim is not None:
        return ToolExecutionResolution(
            state=ToolExecutionState.CLAIMED,
            arguments_digest=arguments_digest,
            tool_name=recorded_name,
        )
    return ToolExecutionResolution(state=ToolExecutionState.NEVER_STARTED)


def stable_execution_id(session_id: Any, tool_call_id: str) -> str:
    """Deterministic execution id from (session, tool_call_id).

    A resumed replay of the same logical call lands on the SAME id and can
    find its prior receipt; when either part is missing the caller falls
    back to a random id (receipt matching is then unavailable).
    """

    if session_id and tool_call_id:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"tool-exec:{session_id}:{tool_call_id}"))
    return str(uuid.uuid4())


@dataclass(frozen=True)
class ActionClaimVerdict:
    """Result of a ledger claim attempt for one tool execution.

    ``claimed`` — this executor owns the first-execution right.
    ``resolution`` — the loser's recovery verdict (``None`` for winners).
    ``conflict`` — human-readable reason when the recorded identity (tool
    name or arguments digest) disagrees with this dispatch; the call is
    withheld without executing.
    """

    claimed: bool
    resolution: ToolExecutionResolution | None = None
    conflict: str | None = None


class ActionLedgerHook(ABC):
    """Optional mirror of tool executions into a persistent action ledger.

    Implementations persist intent (action key, tool name, arguments digest,
    side-effect class, correlation identity) BEFORE business dispatch, claim
    atomically (at most one concurrent claimer), and record the real outcome
    — including the result content, which recovery backfills when channel
    history is gone (post-restart). Outcome-write failures must leave the
    ledger claimed (pending reconciliation), never fabricate a receipt.
    """

    @abstractmethod
    async def resolve(self, *, session_id: str, execution_id: str) -> ToolExecutionResolution:
        """Recovery lookup for one execution from the persistent ledger."""

    @abstractmethod
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
        """Persist intent (idempotent) and claim atomically in one call."""

    @abstractmethod
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
        """Record the real outcome; ``result`` carries the raw tool output."""


def withheld_result(tc_id: str, content: str) -> dict[str, Any]:
    """Terminal tool-result message for a dispatch that must not run."""

    return {"role": "tool", "tool_call_id": tc_id, "content": content, "is_error": True}


def recovery_decision(
    resolution: ToolExecutionResolution,
    *,
    tc_id: str,
    tool_name: str,
    arguments_digest: str,
    classification: SideEffectClass,
    messages: list[dict],
    recorded_content: str | None = None,
) -> dict[str, Any] | None:
    """Map one recovery resolution to a dispatch decision (shared table).

    Returns ``None`` when execution may proceed, or a terminal tool-result
    message when the dispatch must not run. This one function serves both
    authorities — the runtime event log and a persistent action ledger — so
    the decision semantics cannot drift between them. Backfill order for a
    succeeded execution: channel history, then the ledger's recorded real
    content, then an explicit reconciliation marker — never placeholder
    text, never a re-run. Decisions by state:

    - succeeded → backfill the real result; never re-execute
    - outcome_unknown → human review
    - claimed → readonly re-execution; every write class stops for review
    - failed → ``should_auto_retry`` (readonly/idempotent only)
    - store_unavailable → readonly proceeds; side effects fail closed
    """

    if resolution.tool_name is not None and resolution.tool_name != tool_name:
        return withheld_result(tc_id, "[conflict] tool differs from the recorded execution; execution withheld")
    if resolution.arguments_digest is not None and resolution.arguments_digest != arguments_digest:
        logger.warning(
            "Tool execution arguments digest mismatch — conflict, not executed",
        )
        return withheld_result(
            tc_id,
            "[conflict] arguments differ from the recorded execution for this call id; execution withheld",
        )
    state = resolution.state
    if state not in (ToolExecutionState.NEVER_STARTED, ToolExecutionState.STORE_UNAVAILABLE) and (
        resolution.tool_name is None or resolution.arguments_digest is None
    ):
        return withheld_result(tc_id, "[needs review] recorded execution identity incomplete; retry withheld")
    if state is ToolExecutionState.NEVER_STARTED:
        return None
    if state is ToolExecutionState.SUCCEEDED:
        for msg in reversed(messages):
            if msg.get("role") == "tool" and msg.get("tool_call_id") == tc_id:
                return dict(msg)
        if recorded_content is not None:
            return {"role": "tool", "tool_call_id": tc_id, "content": recorded_content}
        digest_suffix = f" (result_digest={resolution.result_digest})" if resolution.result_digest else ""
        logger.warning(
            "Tool execution recorded succeeded but result content is unavailable — reconciliation required",
        )
        return withheld_result(
            tc_id,
            "[reconciliation required] execution recorded succeeded but recorded result "
            f"content is unavailable{digest_suffix}; not re-executed",
        )
    if state is ToolExecutionState.OUTCOME_UNKNOWN:
        return withheld_result(
            tc_id,
            "[needs review] previous execution outcome indeterminate; retry withheld",
        )
    if state is ToolExecutionState.CLAIMED:
        if classification is SideEffectClass.READONLY:
            return None
        return withheld_result(
            tc_id,
            "[needs review] dispatch recorded but no outcome receipt (interrupted execution); retry withheld",
        )
    if state is ToolExecutionState.FAILED:
        if should_auto_retry(classification, RECEIPT_FAILED):
            return None
        return withheld_result(
            tc_id,
            "[needs review] previous execution failed; retry withheld for this side-effect class",
        )
    # STORE_UNAVAILABLE: fail closed for anything side-effecting.
    if classification is SideEffectClass.READONLY:
        return None
    return withheld_result(
        tc_id,
        "[withheld] tool receipt store unavailable; side-effecting execution withheld",
    )
