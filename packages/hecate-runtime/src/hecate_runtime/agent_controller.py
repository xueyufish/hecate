"""Recoverable multi-turn agent controller over the accepted-turn stack.

The controller is the graph-free decision loop: it repeatedly accepts one
model decision, dispatches its governed actions, projects the real results
into the next model input and continues — until a final answer, a
persistent wait signal (which propagates untouched to the host) or an
explicit stop. It owns no storage of its own: the continuation position is
a projection of the authoritative accepted-decision chain. Turn identity
is deterministic (``<prefix>-<index>``) and each turn's ``input_refs`` pin
the frozen config/tool/base-input versions, so recovery re-enters the same
loop from turn one — committed turns resume from storage with zero model
calls and their completed actions replay from the action ledger — and the
first uncommitted turn is where fresh work resumes. A restart therefore
cannot reset the turn budget, re-ask an accepted decision or re-execute a
confirmed action; there is no second action ledger and no in-memory-only
cursor. The outcome speaks only for the loop: a final answer is an
accepted model output, never a Task completion or acceptance decision.

Input revisions extend the same projection: each accepted human input is
an immutable revision with its causal turn boundary (``effective_turn``).
A revision rides between the projections of the turns that already
committed when it was accepted and the first turn that may consume it —
never before earlier turns — and every decision pins the ordered revision
ids it can see in ``input_refs["inputs"]`` and binds their content and causal
boundaries with ``input_refs["input_digests"]``. Committed decisions therefore
restore by their original references no matter how many revisions arrive
later; the key is absent while a turn sees no revision, which keeps
pre-revision ``agent-loop/1`` chains replayable byte-for-byte.
"""

from __future__ import annotations

import abc
import copy
import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from hecate_runtime.accepted_decision import (
    DecisionOwnership,
    DecisionStoreUnavailableError,
    DecisionValidationError,
    RunRef,
    canonical_json,
)
from hecate_runtime.agent_harness import HarnessSpec
from hecate_runtime.agent_turn import AgentTurn, AgentTurnOutcome
from hecate_runtime.decision_step import DecisionResponder
from hecate_runtime.delivery_acceptance import (
    AcceptanceDeclaration,
    continuation_feedback_payload,
    validate_acceptance_record,
)
from hecate_runtime.execution_driver import ExecutionDriver
from hecate_runtime.harness_turn import HarnessTurnPipeline, ModelCallRejectedError
from hecate_runtime.turn_projection import project_turn_messages

__all__ = [
    "CONTROLLER_LOOP_MARKER",
    "AgentController",
    "AgentControllerOutcome",
    "ControllerContinuation",
    "ControllerContinuationPolicy",
    "ControllerExecutionDriver",
    "ControllerInputRevision",
    "ControllerLimits",
    "NoProgressPolicy",
    "ControllerRunSpec",
    "PortTurnResponderSource",
    "TurnResponderSource",
    "decision_step_id_for_turn",
]

CONTROLLER_LOOP_MARKER = "agent-loop/1"

_TURN_STARTED = "controller/turn_started"
_TURN_COMPLETED = "controller/turn_completed"
_FINAL_ANSWER = "controller/final_answer"
_STOPPED = "controller/stopped"
_CONTINUATION = "controller/continuation"


def decision_step_id_for_turn(prefix: str, turn_index: int) -> str:
    """Deterministic, sortable decision identity for one loop turn."""
    if (
        not isinstance(prefix, str)
        or not prefix.strip()
        or prefix.strip() != prefix
        or len(prefix) > 200
        or any(not (char.isalnum() or char in "-_.:") for char in prefix)
    ):
        raise DecisionValidationError(
            "decision prefix must be nonblank, trimmed, at most 200 characters, "
            "and use only letters, digits, '-', '_', '.', ':'"
        )
    if type(turn_index) is not int or turn_index < 1 or turn_index > 10**6:
        raise DecisionValidationError("turn index must be a positive integer")
    return f"{prefix}-{turn_index:06d}"


@dataclass(frozen=True)
class ControllerLimits:
    """Loop-level bounds; hosts own budgets, deadlines and cancellation.

    ``max_turns`` bounds the durable turn index, so a restart cannot reset
    it — replayed turns count against the same budget. ``deadline`` is an
    absolute ISO-8601 instant enforced before every turn (including
    replays of committed turns, whose pending actions are still new work).
    """

    max_turns: int = 16
    deadline: str | None = None

    def __post_init__(self) -> None:
        if type(self.max_turns) is not int or self.max_turns < 1:
            raise DecisionValidationError("max_turns must be a positive integer")
        if self.deadline is not None:
            try:
                datetime.fromisoformat(self.deadline)
            except (TypeError, ValueError) as exc:
                raise DecisionValidationError("deadline must be an ISO-8601 timestamp") from exc


@dataclass(frozen=True)
class NoProgressPolicy:
    """Explicit, first-version no-progress observation policy.

    The declaring task names the tool calls worth watching (``observations``
    — read-only tools only) and how many consecutive identical observation
    turns the loop tolerates (``limit``). Everything else is deliberately
    out of scope: observations judge RECORDED FACTS — the accepted action's
    tool, canonical arguments and the action ledger's stable result digest —
    never tool-name repetition, model text or Todo state; any succeeded
    action outside the observation set makes the window inapplicable and resets the
    window; approval/input waits and their wakeups are control facts that
    never count as observations. ``version`` and the content digest are
    pinned by every decision, so a policy rewritten under the same version
    conflicts on replay instead of reinterpreting committed turns.
    """

    limit: int
    observations: tuple[str, ...]
    version: int = 1

    def __post_init__(self) -> None:
        if type(self.limit) is not int or self.limit < 1:
            raise DecisionValidationError("no-progress limit must be a positive integer")
        if type(self.version) is not int or self.version < 1:
            raise DecisionValidationError("no-progress policy version must be a positive integer")
        if not isinstance(self.observations, tuple) or not self.observations:
            raise DecisionValidationError("no-progress observations must be a nonempty list")
        for name in self.observations:
            if not isinstance(name, str) or not name.strip() or name.strip() != name or len(name) > 256:
                raise DecisionValidationError(
                    "no-progress observation tools must be nonblank, trimmed strings of at most 256 characters"
                )
        if len(set(self.observations)) != len(self.observations):
            raise DecisionValidationError("no-progress observation tools must be unique")

    @property
    def sha256(self) -> str:
        """Content digest over version, limit and the observation set."""
        return hashlib.sha256(
            canonical_json(
                {"version": self.version, "limit": self.limit, "observations": sorted(self.observations)}
            ).encode("utf-8")
        ).hexdigest()

    @classmethod
    def from_declaration(cls, declaration: Any) -> NoProgressPolicy | None:
        """Validate a host's declaration (JSON) into a policy; ``None`` when absent."""
        if declaration is None:
            return None
        if not isinstance(declaration, dict):
            raise DecisionValidationError("no-progress declaration must be an object")
        if set(declaration) - {"limit", "observations", "version"}:
            raise DecisionValidationError("unknown no-progress declaration fields")
        observations = declaration.get("observations")
        if not isinstance(observations, list):
            raise DecisionValidationError("no-progress observations must be a list")
        return cls(
            limit=declaration.get("limit"),
            observations=tuple(observations),
            version=declaration.get("version", 1),
        )

    def validate_observations(self, host_readonly_tools: tuple[str, ...] = ()) -> None:
        """Require kernel or trusted-host evidence of each tool's read-only role."""
        from hecate_runtime.tool_side_effects import SideEffectClass, classify

        if not isinstance(host_readonly_tools, tuple) or any(
            not isinstance(name, str) or not name.strip() for name in host_readonly_tools
        ):
            raise DecisionValidationError("host read-only observations must be a tuple of tool names")
        if any(
            classify(name) is not SideEffectClass.READONLY and name not in host_readonly_tools
            for name in self.observations
        ):
            raise DecisionValidationError("no-progress observations require confirmed read-only tools")


@dataclass(frozen=True)
class ControllerContinuationPolicy:
    """Explicit, first-version bounded continuation after acceptance failure.

    Declaring the policy lets a host re-enter the loop after its delivery
    gate rejected a final answer under a declared acceptance standard —
    never after unverified checks, cancels, budget/deadline stops or
    no-progress stops. ``max_continuations`` bounds the total number of
    persisted corrections for one task across restarts; the count is
    durable host state, not loop memory. The policy is only meaningful
    with a declared acceptance standard (the loop refuses the combination
    without one), and its version and content digest are pinned by every
    decision, so a policy rewritten under the same version conflicts on
    replay instead of silently extending an old chain's budget.
    """

    max_continuations: int
    version: int = 1

    def __post_init__(self) -> None:
        if type(self.max_continuations) is not int or self.max_continuations < 1:
            raise DecisionValidationError("max_continuations must be a positive integer")
        if type(self.version) is not int or self.version < 1:
            raise DecisionValidationError("continuation policy version must be a positive integer")

    @property
    def sha256(self) -> str:
        """Content digest over version and the continuation bound."""
        return hashlib.sha256(
            canonical_json({"version": self.version, "max_continuations": self.max_continuations}).encode("utf-8")
        ).hexdigest()

    @classmethod
    def from_declaration(cls, declaration: Any) -> ControllerContinuationPolicy | None:
        """Validate a host's declaration (JSON) into a policy; ``None`` when absent."""
        if declaration is None:
            return None
        if not isinstance(declaration, dict):
            raise DecisionValidationError("continuation declaration must be an object")
        if set(declaration) - {"max_continuations", "version"}:
            raise DecisionValidationError("unknown continuation declaration fields")
        return cls(max_continuations=declaration.get("max_continuations"), version=declaration.get("version", 1))


@dataclass(frozen=True)
class ControllerContinuation:
    """One persisted acceptance-failure correction, projected at its boundary.

    ``attempt`` is the 1-based continuation index (durable host fact);
    ``candidate_decision_step_id``/``candidate_digest`` pin the exact
    committed final answer the feedback corrects; ``effective_turn`` is the
    first turn that may consume the feedback (the candidate turn + 1 — the
    loop verifies the pairing against the deterministic step ids);
    ``feedback`` is the persisted internal control payload projected
    verbatim as one user message — never a human input revision and never
    part of the frozen base input.
    """

    attempt: int
    candidate_decision_step_id: str
    candidate_digest: str
    effective_turn: int
    feedback: dict[str, Any]
    acceptance_record: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if type(self.attempt) is not int or self.attempt < 1:
            raise DecisionValidationError("continuation attempt must be a positive integer")
        if (
            not isinstance(self.candidate_decision_step_id, str)
            or not self.candidate_decision_step_id.strip()
            or self.candidate_decision_step_id.strip() != self.candidate_decision_step_id
            or len(self.candidate_decision_step_id) > 256
        ):
            raise DecisionValidationError(
                "candidate_decision_step_id must be a nonblank, trimmed string of at most 256 characters"
            )
        if (
            not isinstance(self.candidate_digest, str)
            or len(self.candidate_digest) != 64
            or any(char not in "0123456789abcdef" for char in self.candidate_digest)
        ):
            raise DecisionValidationError("candidate_digest must be a 64-character hex digest")
        if type(self.effective_turn) is not int or self.effective_turn < 1 or self.effective_turn > 10**6:
            raise DecisionValidationError("effective_turn must be a positive integer")
        if not isinstance(self.feedback, dict):
            raise DecisionValidationError("continuation feedback must be a JSON object")
        canonical_json(self.feedback)

    @classmethod
    def from_record(cls, record: Any) -> ControllerContinuation:
        """Rebuild the entry from a persisted trigger fact (strict shape).

        The five loop-bearing fields are required and validated; the
        record's known host-metadata fields (standard binding, verdict
        status, policy digest) are accepted and ignored here, and anything
        else is storage corruption: the host stops for reconciliation
        instead of projecting an invented message.
        """
        if not isinstance(record, dict):
            raise DecisionValidationError("continuation attempt record must be an object")
        unknown = set(record) - {
            "attempt",
            "candidate_decision_step_id",
            "candidate_digest",
            "effective_turn",
            "feedback",
            "standard_version",
            "standard_sha256",
            "status",
            "policy_sha256",
            "acceptance_record",
        }
        if unknown:
            raise DecisionValidationError(f"unknown continuation attempt record fields: {sorted(unknown)}")
        missing = {"attempt", "candidate_decision_step_id", "candidate_digest", "effective_turn", "feedback"} - set(
            record
        )
        if missing:
            raise DecisionValidationError(f"continuation attempt record is missing fields: {sorted(missing)}")
        feedback = record["feedback"]
        if not isinstance(feedback, dict):
            raise DecisionValidationError("continuation feedback must be a JSON object")
        return cls(
            attempt=record["attempt"],
            candidate_decision_step_id=record["candidate_decision_step_id"],
            candidate_digest=record["candidate_digest"],
            effective_turn=record["effective_turn"],
            feedback=json.loads(canonical_json(feedback)),
            acceptance_record=json_copy(record["acceptance_record"])
            if isinstance(record.get("acceptance_record"), dict)
            else None,
        )

    @classmethod
    def from_block(
        cls,
        block: Any,
        policy: ControllerContinuationPolicy | None,
        acceptance: AcceptanceDeclaration | None,
        *,
        task_ref: dict[str, str],
        run_ref: dict[str, str],
    ) -> tuple[ControllerContinuation, ...]:
        """Restore host trigger facts without rerunning their historical checks.

        The retained receipt binds checker semantics, standard, candidate and
        namespace; feedback must reproduce that receipt exactly. Missing or
        inconsistent evidence requires reconciliation before any fresh work.
        """
        if block is None:
            return ()
        if policy is None or acceptance is None:
            raise ValueError("stored continuations require their declared policy and standard")
        if not isinstance(block, dict) or not isinstance(block.get("attempts"), list):
            raise ValueError("stored continuation block is corrupt")
        if block.get("policy_sha256") != policy.sha256 or block.get("max_continuations") != policy.max_continuations:
            raise ValueError("stored continuation policy does not match the declared policy")
        if len(block["attempts"]) > policy.max_continuations:
            raise ValueError("stored continuation attempts exceed the declared bound")
        entries = []
        for index, item in enumerate(block["attempts"], start=1):
            entry = cls.from_record(item)
            receipt = item.get("acceptance_record")
            if not isinstance(receipt, dict):
                raise ValueError("continuation acceptance receipt is missing")
            validate_acceptance_record(receipt)
            if (
                entry.attempt != index
                or item.get("policy_sha256") != policy.sha256
                or item.get("standard_version") != acceptance.version
                or item.get("standard_sha256") != acceptance.sha256
                or item.get("status") != "acceptance_failed"
                or receipt.get("status") != "acceptance_failed"
                or receipt.get("version") != acceptance.version
                or receipt.get("declaration_sha256") != acceptance.sha256
                or receipt.get("task_ref") != task_ref
                or receipt.get("run_ref") != run_ref
                or receipt.get("final_decision")
                != {
                    "decision_step_id": entry.candidate_decision_step_id,
                    "content_digest": entry.candidate_digest,
                }
            ):
                raise ValueError("continuation receipt binding does not match its trigger")
            checks = receipt.get("checks")
            if (
                not isinstance(checks, list)
                or not all(isinstance(check, dict) and check.get("status") in ("passed", "failed") for check in checks)
                or not any(check.get("status") == "failed" for check in checks)
            ):
                raise ValueError("continuation receipt is not a verifiable failure")
            expected = continuation_feedback_payload(receipt, attempt=index, max_continuations=policy.max_continuations)
            if canonical_json(entry.feedback) != canonical_json(expected):
                raise ValueError("continuation feedback does not match its acceptance receipt")
            entries.append(entry)
        return tuple(entries)


@dataclass(frozen=True)
class ControllerInputRevision:
    """One accepted human input with its causal turn boundary.

    ``revision_id`` is the durable identity the wake transaction recorded;
    ``content`` is the model-visible payload (never approval control
    data); ``effective_turn`` is the first turn that may consume it — the
    host records it as one past the committed chain length at acceptance,
    so the revision can never move before turns that already decided.
    """

    revision_id: str
    content: dict[str, Any]
    effective_turn: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.revision_id, str)
            or not self.revision_id.strip()
            or self.revision_id.strip() != self.revision_id
            or len(self.revision_id) > 256
        ):
            raise DecisionValidationError("revision_id must be a nonblank, trimmed string of at most 256 characters")
        if not isinstance(self.content, dict):
            raise DecisionValidationError("revision content must be a JSON object")
        if type(self.effective_turn) is not int or self.effective_turn < 1 or self.effective_turn > 10**6:
            raise DecisionValidationError("effective_turn must be a positive integer")
        canonical_json(self.content)


@dataclass(frozen=True)
class ControllerRunSpec:
    """One controller invocation's host-bound facts.

    ``input_revisions`` carries the durable, ordered input history of the
    run (see :class:`ControllerInputRevision`). Each revision becomes one
    user message at its causal boundary — after the projections of the
    turns committed before its acceptance, before the first turn that may
    consume it — never folded into the frozen base, and each decision pins
    the visible revision ids and content/boundary digests so a later input
    can neither rewrite committed references nor reorder history.
    """

    run_ref: RunRef
    base_messages: list[dict[str, Any]]
    ownership: DecisionOwnership | None = None
    limits: ControllerLimits = field(default_factory=ControllerLimits)
    decision_prefix: str = "agent-turn"
    context: dict[str, Any] | None = None
    execution_context: dict[str, Any] | None = None
    clock: Callable[[], datetime] | None = None
    input_revisions: tuple[ControllerInputRevision, ...] = ()
    # Explicitly declared acceptance standard. Every decision pins its
    # version and digest in ``input_refs``, so a standard modified under
    # the same version number can never reinterpret a committed chain —
    # the replay conflicts and stops for reconciliation instead. Absent
    # (or a bare ``output_schema`` without an acceptance block) pins
    # nothing, keeping pre-binding chains replayable byte-for-byte.
    acceptance: AcceptanceDeclaration | None = None
    # Explicitly declared harness composition (see :mod:`hecate_runtime.agent_harness`).
    # Every decision pins its version and digest, and the base messages
    # must equal the frozen composition — a spec rewritten under the same
    # version conflicts on the first replayed decision instead of silently
    # recomposing. Absent pins nothing, keeping hosts that compose manually
    # (and chains committed before this binding) replayable byte-for-byte.
    harness: HarnessSpec | None = None
    # Host-level stop reasons consulted between actions (before any claim)
    # and between turns; host reasons take precedence over the loop's own
    # cancel/deadline classification. Example: a durable input wait parked
    # mid-turn must stop the turn's remaining actions claim-free.
    stop_check: Callable[[], str | None] | None = None
    # Explicitly declared no-progress policy (see :class:`NoProgressPolicy`).
    # Every decision pins its version and digest; absent pins nothing, so
    # chains committed before the declaration replay byte-for-byte.
    no_progress: NoProgressPolicy | None = None
    # Host attestations affect observation eligibility only, never action
    # authorization, retry classification or caller-supplied policy data.
    readonly_observation_tools: tuple[str, ...] = ()
    # Explicitly declared bounded-continuation policy (see
    # :class:`ControllerContinuationPolicy`). Requires a declared acceptance
    # standard; version and digest ride every decision's input_refs.
    continuation_policy: ControllerContinuationPolicy | None = None
    # Persisted acceptance-failure corrections (durable host facts, see
    # :class:`ControllerContinuation`). Each entry's feedback is projected
    # once at its causal boundary, after the candidate's committed answer;
    # a final answer whose step id and digest match an entry continues the
    # chain instead of exiting, so replays skip already-rejected
    # candidates deterministically. Entries never alter turn budgets,
    # deadlines or the accepted decisions themselves.
    continuations: tuple[ControllerContinuation, ...] = ()


@dataclass(frozen=True)
class AgentControllerOutcome:
    """Facts of one loop pass; never a Task completion decision.

    ``state`` separates an accepted final answer from explicit stops;
    ``reason`` classifies stops (turn cap, deadline, cancellation or the
    governing stop reason of the halted turn). ``turns`` counts the turns
    this pass processed; the durable chain length is what survives. A
    final answer carries its committing decision's identity and digest so
    acceptance records bind the exact accepted output, not a re-read of
    whatever content storage currently holds.
    """

    state: Literal["final_answer", "stopped"]
    reason: str | None
    final_text: str | None
    turns: int
    decision_step_ids: tuple[str, ...] = ()
    final_decision_step_id: str | None = None
    final_decision_digest: str | None = None
    no_progress: dict[str, Any] | None = None

    @property
    def is_final_answer(self) -> bool:
        return self.state == "final_answer"

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe facts for event envelopes; hosts strip nothing."""
        return {
            "state": self.state,
            "reason": self.reason,
            "final_text": self.final_text,
            "turns": self.turns,
            "decision_step_ids": list(self.decision_step_ids),
            "final_decision_step_id": self.final_decision_step_id,
            "final_decision_digest": self.final_decision_digest,
            **({"no_progress": json_copy(self.no_progress)} if self.no_progress is not None else {}),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentControllerOutcome:
        return cls(
            state=data["state"],
            reason=data.get("reason"),
            final_text=data.get("final_text"),
            turns=int(data["turns"]),
            decision_step_ids=tuple(data.get("decision_step_ids") or ()),
            final_decision_step_id=data.get("final_decision_step_id"),
            final_decision_digest=data.get("final_decision_digest"),
            no_progress=json_copy(data["no_progress"]) if data.get("no_progress") is not None else None,
        )


class TurnResponderSource(abc.ABC):
    """Per-turn frozen responder factory over rebuilt message lists.

    The source freezes the model config, tool snapshot and reference
    versions once; the controller hands it each turn's rebuilt messages and
    the exact ``input_refs`` it will pin, so the responder validates the
    same references the decision store commits. Implementations must be
    deterministic in ``frozen_refs`` — a rebuilt turn must present the
    references its committed decision carries.
    """

    @abc.abstractmethod
    def frozen_refs(self) -> dict[str, Any]:
        """Reference versions frozen for the whole loop (config, tools, base input)."""

    @abc.abstractmethod
    def responder_for(self, messages: list[dict[str, Any]], input_refs: dict[str, Any]) -> DecisionResponder:
        """Build the frozen responder for one turn's messages and references."""


class PortTurnResponderSource(TurnResponderSource):
    """Default source over :class:`PortDecisionResponder` snapshots."""

    def __init__(
        self,
        port: Any,
        *,
        config: dict[str, Any],
        tool_definitions: dict[str, Any],
        base_refs: dict[str, Any],
        on_usage: Callable[[dict[str, Any]], None] | None = None,
        pipeline: HarnessTurnPipeline | None = None,
    ) -> None:
        from hecate_runtime.port_decision_responder import PortDecisionResponder

        self._port = port
        self._config = json_copy(config)
        self._tool_definitions = json_copy(tool_definitions)
        self._base_refs = json_copy(base_refs)
        self._base_refs["model_snapshot"] = hashlib.sha256(canonical_json(self._config).encode()).hexdigest()
        self._base_refs["tool_snapshot"] = hashlib.sha256(canonical_json(self._tool_definitions).encode()).hexdigest()
        self._on_usage = on_usage
        self._pipeline = pipeline
        if pipeline is not None:
            self._base_refs["harness_pipeline"] = pipeline.frozen_refs()
        # Freeze once: every turn's responder shares one config/tool
        # snapshot, and an invalid combination fails before any turn runs.
        PortDecisionResponder(
            port,
            messages=[{"role": "system", "content": "frozen"}],
            config=config,
            tool_definitions=tool_definitions,
            expected_refs=base_refs,
        )

    def frozen_refs(self) -> dict[str, Any]:
        return json_copy(self._base_refs)

    def responder_for(self, messages: list[dict[str, Any]], input_refs: dict[str, Any]) -> DecisionResponder:
        from hecate_runtime.port_decision_responder import PortDecisionResponder

        return PortDecisionResponder.for_turn_messages(
            self._port,
            messages=messages,
            config=self._config,
            tool_definitions=self._tool_definitions,
            expected_refs=input_refs,
            on_usage=self._on_usage,
            pipeline=self._pipeline,
        )


def json_copy(value: Any) -> Any:
    """Detach a JSON value; non-JSON content is a validation error."""
    return json.loads(canonical_json(value))


class AgentController:
    """Drive the accepted-turn loop until a final answer, wait or stop.

    Pure composition over :class:`AgentTurn` and
    :func:`project_turn_messages`; all durability, ownership and governed
    execution belong to those components and the shared stores they hold.
    """

    def __init__(self, turn: AgentTurn, responder_source: TurnResponderSource) -> None:
        self._turn = turn
        self._source = responder_source

    async def run(
        self,
        spec: ControllerRunSpec,
        *,
        should_cancel: Callable[[], bool] | None = None,
        emit: Callable[[dict[str, Any]], None] | None = None,
    ) -> AgentControllerOutcome:
        """Run the loop to its end; see :meth:`stream` for incremental events."""
        outcome: AgentControllerOutcome | None = None
        async for event in self.stream(spec, should_cancel=should_cancel):
            if emit is not None:
                emit(event)
            if event.get("type") in (_FINAL_ANSWER, _STOPPED):
                outcome = AgentControllerOutcome.from_dict(event["outcome"])
        if outcome is None:  # pragma: no cover - stream always ends with one
            raise DecisionValidationError("controller stream ended without an outcome")
        return outcome

    def stream(
        self,
        spec: ControllerRunSpec,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield per-turn events; the terminal event carries the outcome."""
        return self._iterate(spec, should_cancel)

    async def _iterate(
        self, spec: ControllerRunSpec, should_cancel: Callable[[], bool] | None
    ) -> AsyncIterator[dict[str, Any]]:
        if not isinstance(spec, ControllerRunSpec):
            raise DecisionValidationError("spec must be a ControllerRunSpec")
        if not isinstance(spec.run_ref, RunRef):
            raise DecisionValidationError("spec.run_ref must be a RunRef")
        base_messages = copy.deepcopy(self._validated_messages(spec.base_messages))
        clock = spec.clock or (lambda: datetime.now(UTC))
        deadline = self._parsed_deadline(spec.limits.deadline)

        def should_stop() -> str | None:
            if spec.stop_check is not None:
                host_reason = spec.stop_check()
                if host_reason is not None:
                    return host_reason
            if should_cancel is not None and should_cancel():
                return "cancel_requested"
            if deadline is not None and clock() >= deadline:
                return "deadline_exceeded"
            return None

        frozen = json_copy(self._source.frozen_refs())
        frozen["base_input"] = hashlib.sha256(canonical_json(base_messages).encode()).hexdigest()
        if spec.acceptance is not None:
            spec.acceptance.to_dict()
            # The acceptance binding is identifiable per decision: only an
            # explicitly declared standard pins version and digest, so
            # chains committed before the binding (or without a declared
            # standard) replay byte-for-byte, and a same-version standard
            # rewrite conflicts on the first replayed decision.
            frozen["acceptance_version"] = spec.acceptance.version
            frozen["acceptance_digest"] = spec.acceptance.sha256
        if spec.harness is not None:
            spec.harness.to_dict()
            # The harness binding is identifiable per decision and binds the
            # rendered input, not just the plan: base messages that drift
            # from the frozen composition are a validation error before any
            # turn runs, and a spec rewritten under the same version
            # conflicts on the first replayed decision.
            if canonical_json(base_messages) != canonical_json(spec.harness.compose_messages()):
                raise DecisionValidationError("base_messages must equal the frozen harness composition")
            frozen["harness_version"] = spec.harness.version
            frozen["harness_digest"] = spec.harness.sha256
        if spec.no_progress is not None:
            # The no-progress binding is identifiable per decision: version
            # and content digest ride every input_refs, so a policy
            # rewritten under the same version conflicts on replay.
            frozen["no_progress_version"] = spec.no_progress.version
            frozen["no_progress_digest"] = spec.no_progress.sha256
            spec.no_progress.validate_observations(spec.readonly_observation_tools)
            if spec.readonly_observation_tools:
                frozen["no_progress_readonly_tools"] = sorted(set(spec.readonly_observation_tools))
        continuation_by_candidate: dict[str, ControllerContinuation] = {}
        if spec.continuation_policy is not None:
            # The continuation binding is identifiable per decision: version
            # and content digest ride every input_refs, so a policy
            # rewritten under the same version conflicts on replay. Without
            # a declared acceptance standard there is no frozen standard to
            # correct against — the combination is a validation error, not
            # an unbounded revision channel.
            if spec.acceptance is None:
                raise DecisionValidationError("a continuation policy requires a declared acceptance standard")
            frozen["continuation_version"] = spec.continuation_policy.version
            frozen["continuation_digest"] = spec.continuation_policy.sha256
        if spec.continuations:
            if spec.continuation_policy is None:
                raise DecisionValidationError("continuation entries require a declared policy")
            if len(spec.continuations) > spec.continuation_policy.max_continuations:
                raise DecisionValidationError("continuation entries exceed the declared bound")
            # Entries must pair with the loop's deterministic step ids: the
            # feedback enters exactly one turn after its candidate, and
            # candidates never repeat. A mismatched entry is a spec/storage
            # inconsistency, not a reordering opportunity.
            seen_candidates: set[str] = set()
            previous_boundary = 0
            for index, entry in enumerate(spec.continuations, start=1):
                if entry.attempt != index:
                    raise DecisionValidationError("continuation attempts must be consecutive from one")
                if decision_step_id_for_turn(spec.decision_prefix, entry.effective_turn - 1) != (
                    entry.candidate_decision_step_id
                ):
                    raise DecisionValidationError(
                        "continuation effective_turn must be exactly one turn after its candidate decision"
                    )
                if entry.candidate_decision_step_id in seen_candidates:
                    raise DecisionValidationError("continuation entries must reference distinct candidates")
                if entry.effective_turn <= previous_boundary:
                    raise DecisionValidationError("continuation entries must be ordered by effective_turn")
                seen_candidates.add(entry.candidate_decision_step_id)
                previous_boundary = entry.effective_turn
                continuation_by_candidate[entry.candidate_decision_step_id] = entry
                await self._turn.validate_final_answer(
                    spec.run_ref, entry.candidate_decision_step_id, entry.candidate_digest
                )
        # Stable causal order: by effective boundary, then given (durable
        # seq) order inside one boundary. Ids are unique — two revisions
        # can share a boundary, never an identity.
        revisions = sorted(
            (
                ControllerInputRevision(item.revision_id, json_copy(item.content), item.effective_turn)
                for item in spec.input_revisions
            ),
            key=lambda item: item.effective_turn,
        )
        seen_ids: set[str] = set()
        for revision in revisions:
            if revision.revision_id in seen_ids:
                raise DecisionValidationError(f"duplicate input revision id {revision.revision_id!r}")
            seen_ids.add(revision.revision_id)
        pending_revisions = list(revisions)
        messages = base_messages
        committed: list[str] = []
        turn_index = 1
        # No-progress observation window: consecutive identical observation
        # turns, rebuilt from durable facts as turns replay — a restart
        # re-derives the same series and stops at the same place. A new
        # input revision opens a fresh window WITHOUT resetting the turn
        # budget, deadline or policy.
        no_progress_streak = 0
        previous_signature: str | None = None
        while True:
            # Stop gates run before every turn — replays included: a replayed
            # turn's pending actions are still undispatched work, and the
            # durable turn budget must survive restarts unchanged.
            if should_cancel is not None and should_cancel():
                yield self._terminal(_STOPPED, committed, turn_index - 1, reason="cancel_requested")
                return
            if deadline is not None and clock() >= deadline:
                yield self._terminal(_STOPPED, committed, turn_index - 1, reason="deadline_exceeded")
                return
            if turn_index > spec.limits.max_turns:
                yield self._terminal(_STOPPED, committed, turn_index - 1, reason="turn_limit")
                return
            # Revisions enter at their causal boundary: after the turns that
            # committed before acceptance, before this turn's ask. A later
            # input can never move before an earlier turn's projection.
            while pending_revisions and pending_revisions[0].effective_turn <= turn_index:
                revision = pending_revisions.pop(0)
                messages = messages + [
                    {
                        "role": "user",
                        "content": json.dumps({"provided_input": revision.content}, ensure_ascii=False),
                    }
                ]
            step_id = decision_step_id_for_turn(spec.decision_prefix, turn_index)
            input_refs = {**frozen, "controller": CONTROLLER_LOOP_MARKER, "turn": turn_index}
            visible_continuations = [entry for entry in spec.continuations if entry.effective_turn <= turn_index]
            if visible_continuations:
                # Bind every consumed feedback payload to the next decision,
                # without changing the rejected candidate's original refs.
                input_refs["continuation_digests"] = [
                    hashlib.sha256(
                        canonical_json(
                            {
                                "attempt": entry.attempt,
                                "candidate_decision_step_id": entry.candidate_decision_step_id,
                                "candidate_digest": entry.candidate_digest,
                                "effective_turn": entry.effective_turn,
                                "feedback": entry.feedback,
                            }
                        ).encode("utf-8")
                    ).hexdigest()
                    for entry in visible_continuations
                ]
            visible_inputs = [item.revision_id for item in revisions if item.effective_turn <= turn_index]
            if visible_inputs:
                # The revision binding is identifiable per decision: only
                # present when this turn actually sees a revision, so
                # pre-revision agent-loop/1 chains replay byte-for-byte.
                input_refs["inputs"] = visible_inputs
                input_refs["input_digests"] = [
                    hashlib.sha256(
                        canonical_json(
                            {"id": item.revision_id, "content": item.content, "effective_turn": item.effective_turn}
                        ).encode("utf-8")
                    ).hexdigest()
                    for item in revisions
                    if item.effective_turn <= turn_index
                ]
            yield {
                "type": _TURN_STARTED,
                "turn": turn_index,
                "decision_step_id": step_id,
                "input_refs": json_copy(input_refs),
            }
            responder = self._source.responder_for(copy.deepcopy(messages), json_copy(input_refs))
            try:
                outcome = await self._turn.run(
                    run_ref=spec.run_ref,
                    decision_step_id=step_id,
                    input_refs=input_refs,
                    ownership=spec.ownership,
                    context=spec.context,
                    execution_context=spec.execution_context,
                    responder=responder,
                    should_stop=should_stop,
                )
            except ModelCallRejectedError as exc:
                # A guard refused this turn's model call: the turn commits
                # no decision (a canned refusal is never an accepted model
                # output), and the loop classifies the stop instead of
                # crashing — the rejected turn re-attempts on a re-entry.
                yield self._terminal(
                    _STOPPED,
                    committed,
                    turn_index - 1,
                    reason=exc.reason if exc.stage == "context" else "model_call_rejected",
                    detail=f"{exc.stage}: {exc.reason}",
                )
                return
            committed.append(step_id)
            if outcome.is_final_answer:
                entry = continuation_by_candidate.get(step_id)
                if entry is not None:
                    # An explicitly persisted correction for this committed
                    # candidate: project the delivered answer and the stored
                    # feedback, then continue the same chain. The digest
                    # must match the committed decision — a candidate
                    # rewritten under the same step id is corruption, not a
                    # revision. The candidate's accepted decision stays
                    # immutable; the feedback is an internal control
                    # message, never a human input revision.
                    if entry.candidate_digest != outcome.record.content_digest:
                        raise DecisionStoreUnavailableError(
                            "continuation candidate digest does not match the committed decision"
                        )
                    candidate_text = text_or_none(outcome)
                    messages = messages + [
                        {"role": "assistant", "content": candidate_text if isinstance(candidate_text, str) else ""},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {"continuation_feedback": json_copy(entry.feedback)}, ensure_ascii=False
                            ),
                        },
                    ]
                    # A failed candidate and internal feedback do not establish
                    # progress. Carry the observation window across corrections.
                    yield {
                        "type": _CONTINUATION,
                        "turn": turn_index,
                        "attempt": entry.attempt,
                        "candidate_decision_step_id": step_id,
                    }
                    turn_index += 1
                    continue
                final = AgentControllerOutcome(
                    state="final_answer",
                    reason=None,
                    final_text=text_or_none(outcome),
                    turns=turn_index,
                    decision_step_ids=tuple(committed),
                    final_decision_step_id=outcome.record.decision_step_id,
                    final_decision_digest=outcome.record.content_digest,
                )
                yield {
                    "type": _FINAL_ANSWER,
                    "turn": turn_index,
                    "text": final.final_text,
                    "outcome": final.to_dict(),
                }
                return
            yield {
                "type": _TURN_COMPLETED,
                "turn": turn_index,
                "decision_step_id": step_id,
                "statuses": [item.status for item in outcome.report.outcomes],
                "unprocessed_action_keys": list(outcome.report.unprocessed_action_keys),
            }
            # A committed final answer is never reclassified by a later
            # stop; host stops (e.g. a mid-turn durable wait) outrank the
            # loop's own classification for action turns.
            stop = should_stop() or self._turn_stop_reason(outcome)
            if stop is not None:
                yield self._terminal(_STOPPED, committed, turn_index, reason=stop)
                return
            # Validate the complete, identity-bound report before any
            # observation can classify the turn as a no-progress stop.
            messages = project_turn_messages(outcome, messages)
            if spec.no_progress is not None:
                # Consecutive identical observation turns stop the loop
                # before the next model call. A succeeded action outside the
                # observation set resets this scoped window; otherwise the recorded
                # facts — tool, arguments, ledger result digest — form the
                # turn's observation. A human input revision opens a fresh
                # window (control facts never fabricate progress); total
                # turns, budget and deadline are untouched.
                if any(item.effective_turn == turn_index for item in revisions):
                    no_progress_streak = 0
                    previous_signature = None
                signature, saw_unobserved = self._observation_signature(
                    spec.no_progress, outcome, spec.readonly_observation_tools
                )
                if saw_unobserved or signature is None:
                    no_progress_streak = 0
                    previous_signature = None
                elif signature == previous_signature:
                    no_progress_streak += 1
                else:
                    no_progress_streak = 1
                    previous_signature = signature
                if no_progress_streak >= spec.no_progress.limit:
                    yield self._terminal(
                        _STOPPED,
                        committed,
                        turn_index,
                        reason="no_progress_limit",
                        detail=f"no-progress observations reached the declared limit "
                        f"({no_progress_streak} consecutive identical observation turns)",
                        no_progress={
                            "policy_version": spec.no_progress.version,
                            "policy_sha256": spec.no_progress.sha256,
                            "limit": spec.no_progress.limit,
                            "consecutive_turns": no_progress_streak,
                            "through_turn": turn_index,
                            "last_decision_step_id": step_id,
                            "observation_sha256": signature,
                        },
                    )
                    return
            turn_index += 1

    @staticmethod
    def _validated_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(messages, list) or any(not isinstance(message, dict) for message in messages):
            raise DecisionValidationError("base_messages must be a list of JSON objects")
        canonical_json(messages)
        return messages

    @staticmethod
    def _parsed_deadline(deadline: str | None) -> datetime | None:
        if deadline is None:
            return None
        parsed = datetime.fromisoformat(deadline)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed

    @staticmethod
    def _turn_stop_reason(outcome: AgentTurnOutcome) -> str | None:
        """Classify a halted action turn; ``None`` means it fully succeeded."""
        report = outcome.report
        if not report.outcomes:
            # An action decision with zero dispatched outcomes cannot project.
            return "no_dispatchable_actions"
        last = report.outcomes[-1]
        if report.unprocessed_action_keys or last.status != "succeeded":
            if last.stop_reason is not None:
                return last.stop_reason
            return f"action_{last.status}"
        return None

    @staticmethod
    def _observation_signature(
        policy: NoProgressPolicy, outcome: AgentTurnOutcome, host_readonly_tools: tuple[str, ...] = ()
    ) -> tuple[str | None, bool]:
        """One fully-succeeded turn's observation signature over recorded facts.

        Returns ``(signature, saw_unobserved)``: the signature hashes the
        ordered observation identities — tool, canonical arguments and the
        ledger's stable result digest per observed succeeded action — and
        ``saw_unobserved`` marks any succeeded action outside the policy
        (outside this scoped window). ``None`` means the turn
        carried no comparable observation fact.
        """
        identities: list[dict[str, Any]] = []
        saw_unobserved = False
        for action, action_outcome in zip(outcome.record.actions, outcome.report.outcomes, strict=True):
            if action_outcome.status != "succeeded":
                continue
            if action_outcome.is_input_request:
                return None, True
            observed = action.tool in policy.observations and (
                action.side_effect_class == "readonly" or action.tool in host_readonly_tools
            )
            if not observed:
                saw_unobserved = True
                continue
            digest = action_outcome.result_digest
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
            ):
                raise DecisionStoreUnavailableError("no-progress observation has no verifiable ledger result digest")
            identities.append(
                {
                    "tool": action.tool,
                    "arguments": action.arguments,
                    "result_digest": action_outcome.result_digest,
                }
            )
        if not identities:
            return None, saw_unobserved
        return hashlib.sha256(canonical_json(identities).encode("utf-8")).hexdigest(), saw_unobserved

    def _terminal(
        self,
        event_type: str,
        committed: list[str],
        turns: int,
        *,
        reason: str,
        detail: str | None = None,
        no_progress: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        outcome = AgentControllerOutcome(
            state="stopped",
            reason=reason,
            final_text=None,
            turns=turns,
            decision_step_ids=tuple(committed),
            no_progress=no_progress,
        )
        event: dict[str, Any] = {"type": event_type, "reason": reason, "outcome": outcome.to_dict()}
        if detail is not None:
            event["detail"] = detail
        return event


def text_or_none(outcome: AgentTurnOutcome) -> str | None:
    """Extract the committed final-answer text without coercing its shape."""
    text = outcome.record.accepted_output.get("text")
    return text if isinstance(text, str) else None


class ControllerExecutionDriver(ExecutionDriver):
    """Adapt one controller invocation to the shared execution service.

    Constructed per run by the host assembler with the controller and its
    spec bound; the shared ``RuntimeExecutionService`` drives the event
    stream, so cooperative cancellation and unknown-outcome stops behave
    exactly like the Pregel driver. After the stream completes the host
    reads :attr:`outcome`; a persistent wait propagates as its signal
    exception (never converted into an outcome here).
    """

    def __init__(
        self, controller: AgentController, spec: ControllerRunSpec, *, should_cancel: Callable[[], bool] | None = None
    ) -> None:
        self._controller = controller
        self._spec = spec
        self._outcome: AgentControllerOutcome | None = None
        self._should_cancel = should_cancel

    @property
    def outcome(self) -> AgentControllerOutcome | None:
        """The loop outcome once the stream terminated; ``None`` otherwise."""
        return self._outcome

    def execute(
        self,
        session_id: uuid.UUID,
        initial_input: dict[str, Any] | None = None,
        execution_mode: str = "conversational",
        stream_mode: Any = None,
        resume_value: Any = None,
    ) -> AsyncIterator[Any]:
        """Yield the controller's events; the terminal event is retained.

        ``session_id``/``initial_input``/``resume_value`` are accepted for
        driver-interface compatibility and deliberately ignored: the run
        facts travel on the spec, and the loop's continuation is the
        accepted-decision chain, not a resumable channel snapshot.
        """
        return self._stream()

    async def _stream(self) -> AsyncIterator[Any]:
        async for event in self._controller.stream(self._spec, should_cancel=self._should_cancel):
            if event.get("type") in (_FINAL_ANSWER, _STOPPED):
                self._outcome = AgentControllerOutcome.from_dict(event["outcome"])
            yield event
