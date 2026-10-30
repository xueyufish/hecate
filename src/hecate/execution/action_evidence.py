"""Read-only evidence receipts for one recorded action.

A receipt is a projection over facts the platform already persists: the
action ledger's intent and outcome rows, and the run's accepted
decisions. It adds no ledger, no write path and no migration — it
re-reads what was written and reports what can be verified now.

The receipt keeps three questions apart, because collapsing them would
overstate what the platform knows:

* *What was recorded* — the action's identity, tool and side-effect
  class, the recorded recovery state and outcome, the argument and
  result digests, and when that outcome was recorded.
* *What a digest covers* — the algorithm and the object that was hashed.
  A matching digest proves only that the stored content still hashes to
  the recorded value. It is never evidence that a business goal passed,
  and it says nothing about work that was never dispatched.
* *What is verifiable right now* — the recomputation outcome: ``verified``,
  ``mismatch``, ``unverifiable`` (nothing recorded to hash) or
  ``unsupported`` (the recorded shape cannot be re-checked here).

Every returned field is whitelisted. Raw arguments, result bodies, error
bodies, session ids, claim tokens, credentials and storage addresses never
enter a receipt, and a recorded ``result_ref`` never authorizes a read on
its own — it is a pointer, not a capability.

The ledger's outcome is the *latest* recorded outcome, not an immutable
historical credential: a receipt states the version it read so a later
re-read is visibly a different reading of the same action.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from hecate_durable.contracts.references import BackendRef
from hecate_runtime.accepted_action_step import execution_id_for_action, tool_call_id_for_action
from hecate_runtime.accepted_decision import RunRef
from hecate_runtime.action_ledger import tool_arguments_digest

logger = logging.getLogger(__name__)

#: The one digest algorithm the platform records and this receipt re-checks:
#: SHA-256 over canonical JSON (sorted keys, compact separators, UTF-8).
EVIDENCE_DIGEST_ALGORITHM = "sha256:canonical-json"

#: What each digest covers. Named so the page can say what was hashed
#: instead of implying a broader guarantee.
ARGUMENTS_SUBJECT = "accepted_decision_action_arguments"
RESULT_SUBJECT = "recorded_result_payload"

DigestStatus = Literal["verified", "mismatch", "unverifiable", "unsupported"]
ResultState = Literal["recorded", "missing", "unknown"]

#: Distinguishes "no outcome was recorded" from "an outcome without content".
_ABSENT: Any = object()


@dataclass(frozen=True)
class DigestVerification:
    """The outcome of re-checking one recorded digest, with its subject."""

    status: DigestStatus
    algorithm: str
    subject: str
    recorded_digest: str | None = None
    computed_digest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return only the digest verification's public metadata."""
        return {
            "status": self.status,
            "algorithm": self.algorithm,
            "subject": self.subject,
            "recorded_digest": self.recorded_digest,
            "computed_digest": self.computed_digest,
        }


@dataclass(frozen=True)
class DecisionReference:
    """The accepted decision this action was dispatched from, if provable.

    ``not_recorded`` is a first-class answer: the action may predate the
    accepted-decision chain, or the chain may no longer resolve it. The
    receipt must not invent a decision reference to fill the gap.
    """

    status: Literal["linked", "not_recorded"]
    decision_step_id: str | None = None
    content_digest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the validated decision reference or its explicit absence."""
        return {
            "status": self.status,
            "decision_step_id": self.decision_step_id,
            "content_digest": self.content_digest,
        }


@dataclass(frozen=True)
class ActionEvidenceReceipt:
    """One action's evidence receipt, whitelisted field by field."""

    task_id: str
    run_id: str
    action_key: str
    execution_id: str | None
    tool_call_id: str | None
    tool_name: str | None
    side_effect_class: str | None
    arguments_digest: str | None
    recovery_state: str | None
    outcome: str | None
    result_digest: str | None
    pending_reconciliation: bool
    outcome_recorded_at: str | None
    result_state: ResultState
    arguments_verification: DigestVerification
    result_verification: DigestVerification
    decision_ref: DecisionReference
    read_at: str
    ledger_source: str

    def to_dict(self) -> dict[str, Any]:
        """Return the receipt as the API's whitelisted payload."""
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "action_key": self.action_key,
            "execution_id": self.execution_id,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "side_effect_class": self.side_effect_class,
            "arguments_digest": self.arguments_digest,
            "recovery_state": self.recovery_state,
            "outcome": self.outcome,
            "result_digest": self.result_digest,
            "pending_reconciliation": self.pending_reconciliation,
            "outcome_recorded_at": self.outcome_recorded_at,
            "result_state": self.result_state,
            "arguments_verification": self.arguments_verification.to_dict(),
            "result_verification": self.result_verification.to_dict(),
            "decision_ref": self.decision_ref.to_dict(),
            "read_at": self.read_at,
            "ledger_source": self.ledger_source,
        }


def verify_digest(*, recorded_digest: str | None, content: Any, subject: str) -> DigestVerification:
    """Re-check one recorded digest against the persisted content.

    ``verified`` means only that the stored content still hashes to the
    recorded value. A missing recorded digest or missing content is
    ``unverifiable`` — the receipt says the check did not happen instead
    of implying a pass. Content this platform cannot re-hash is
    ``unsupported``.
    """
    if content is _ABSENT or recorded_digest is None:
        return DigestVerification("unverifiable", EVIDENCE_DIGEST_ALGORITHM, subject, recorded_digest, None)
    try:
        computed = tool_arguments_digest(content)
    except (TypeError, ValueError):
        # Unserializable content: the recorded shape cannot be re-checked
        # here, which is a fact about the receipt, not a mismatch.
        return DigestVerification("unsupported", EVIDENCE_DIGEST_ALGORITHM, subject, recorded_digest, None)
    status: DigestStatus = "verified" if computed == recorded_digest else "mismatch"
    return DigestVerification(status, EVIDENCE_DIGEST_ALGORITHM, subject, recorded_digest, computed)


def link_decision(
    *,
    runtime_run_ref: RunRef,
    decisions: list[dict[str, Any]],
    tool_call_id: str | None,
    execution_id: str | None,
    action_key: str,
    tool_name: str | None,
) -> tuple[DecisionReference, dict[str, Any] | None]:
    """Find the accepted decision an action was dispatched from.

    The dispatched identity is derived rather than stored: the tool call
    id is a UUID over (run, decision step, action key), and the execution
    id is derived from it. Re-deriving both and matching them against the
    run's accepted decisions is therefore exact, and a row without a tool
    call id simply has no decision to point at. An ambiguous match is
    treated as unlinked rather than guessed.
    """
    if not tool_call_id or not execution_id or not tool_name:
        return DecisionReference("not_recorded"), None
    matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for decision in decisions:
        step_id = decision.get("decision_step_id")
        if not isinstance(step_id, str) or not step_id:
            continue
        for action in decision.get("actions", []):
            if not isinstance(action, dict):
                continue
            key = action.get("action_key")
            if not isinstance(key, str) or not key:
                continue
            if tool_call_id_for_action(runtime_run_ref, step_id, key) != tool_call_id:
                continue
            expected_execution = execution_id_for_action(runtime_run_ref, step_id, key)
            if (
                expected_execution != execution_id
                or action_key != expected_execution
                or action.get("tool") != tool_name
            ):
                continue
            matches.append((decision, action))
    if len(matches) != 1:
        return DecisionReference("not_recorded"), None
    decision, action = matches[0]
    content_digest = decision.get("content_digest")
    return (
        DecisionReference(
            "linked",
            decision["decision_step_id"],
            content_digest if isinstance(content_digest, str) else None,
        ),
        action,
    )


def _result_state(outcome: str | None, payload: Any) -> ResultState:
    """Classify the recorded result: absent, explicitly unknown, or recorded."""
    if outcome is None:
        return "missing"
    if outcome == "unknown":
        return "unknown"
    if payload is _ABSENT or payload is None:
        return "missing"
    return "recorded"


async def read_action_evidence(
    *,
    store: Any,
    run_ref: BackendRef,
    runtime_run_ref: RunRef,
    task_ref: BackendRef,
    action_key: str,
    ledger_source: str,
) -> ActionEvidenceReceipt | None:
    """Read one action's evidence receipt from the recorded facts.

    Returns ``None`` when the run has no such action — a foreign action is
    indistinguishable from an absent one. The read never writes: no
    ledger row, event, model call or tool call is touched.
    """
    action_row = await asyncio.to_thread(store.get_run_action, run_ref, action_key)
    if action_row is None:
        return None
    # The action must belong to exactly this run and parent task; a row
    # that disagrees is treated as absent rather than reported.
    if (
        (action_row.get("task_issuer"), action_row.get("task_id")) != (task_ref.issuer_domain, task_ref.id)
        or (action_row.get("run_issuer"), action_row.get("run_id")) != (run_ref.issuer_domain, run_ref.id)
        or action_row.get("action_key") != action_key
    ):
        return None
    decisions = await asyncio.to_thread(store.list_accepted_actions, run_ref)
    intent = action_row.get("intent") or {}
    outcome = action_row.get("last_outcome") or {}
    outcome_name = outcome.get("outcome")
    payload = action_row.get("result_payload")
    recorded_result_digest = outcome.get("result_digest")
    tool_call_id = action_row.get("tool_call_id")
    execution_id = action_row.get("execution_id")

    decision_ref, decision_action = link_decision(
        runtime_run_ref=runtime_run_ref,
        decisions=decisions,
        tool_call_id=tool_call_id,
        execution_id=execution_id,
        action_key=action_key,
        tool_name=intent.get("action_name"),
    )
    arguments_content = _ABSENT
    if decision_action is not None:
        arguments_content = decision_action.get("arguments", _ABSENT)
    # A null payload is also how legacy or reference-only outcomes represent
    # missing content. There is no stored discriminator for an actual JSON null.
    result_content = _ABSENT if outcome_name is None or payload is None else payload

    return ActionEvidenceReceipt(
        task_id=task_ref.id,
        run_id=run_ref.id,
        action_key=action_key,
        execution_id=execution_id,
        tool_call_id=tool_call_id,
        tool_name=intent.get("action_name"),
        side_effect_class=intent.get("side_effect_class"),
        arguments_digest=intent.get("arguments_digest"),
        recovery_state=action_row.get("state"),
        outcome=outcome_name,
        result_digest=recorded_result_digest,
        pending_reconciliation=bool(action_row.get("pending_reconciliation")),
        outcome_recorded_at=action_row.get("outcome_recorded_at"),
        result_state=_result_state(outcome_name, payload),
        arguments_verification=verify_digest(
            recorded_digest=intent.get("arguments_digest"),
            content=arguments_content,
            subject=ARGUMENTS_SUBJECT,
        ),
        result_verification=verify_digest(
            recorded_digest=recorded_result_digest,
            content=result_content,
            subject=RESULT_SUBJECT,
        ),
        decision_ref=decision_ref,
        read_at=datetime.now(UTC).isoformat(),
        ledger_source=ledger_source,
    )
