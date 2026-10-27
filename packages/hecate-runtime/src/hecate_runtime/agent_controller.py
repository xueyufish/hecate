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
    DecisionValidationError,
    RunRef,
    canonical_json,
)
from hecate_runtime.agent_turn import AgentTurn, AgentTurnOutcome
from hecate_runtime.decision_step import DecisionResponder
from hecate_runtime.delivery_acceptance import AcceptanceDeclaration
from hecate_runtime.execution_driver import ExecutionDriver
from hecate_runtime.turn_projection import project_turn_messages

__all__ = [
    "CONTROLLER_LOOP_MARKER",
    "AgentController",
    "AgentControllerOutcome",
    "ControllerExecutionDriver",
    "ControllerInputRevision",
    "ControllerLimits",
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
    # Host-level stop reasons consulted between actions (before any claim)
    # and between turns; host reasons take precedence over the loop's own
    # cancel/deadline classification. Example: a durable input wait parked
    # mid-turn must stop the turn's remaining actions claim-free.
    stop_check: Callable[[], str | None] | None = None


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
    ) -> None:
        from hecate_runtime.port_decision_responder import PortDecisionResponder

        self._port = port
        self._config = json_copy(config)
        self._tool_definitions = json_copy(tool_definitions)
        self._base_refs = json_copy(base_refs)
        self._base_refs["model_snapshot"] = hashlib.sha256(canonical_json(self._config).encode()).hexdigest()
        self._base_refs["tool_snapshot"] = hashlib.sha256(canonical_json(self._tool_definitions).encode()).hexdigest()
        self._on_usage = on_usage
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
            committed.append(step_id)
            if outcome.is_final_answer:
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
            messages = project_turn_messages(outcome, messages)
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

    def _terminal(self, event_type: str, committed: list[str], turns: int, *, reason: str) -> dict[str, Any]:
        outcome = AgentControllerOutcome(
            state="stopped",
            reason=reason,
            final_text=None,
            turns=turns,
            decision_step_ids=tuple(committed),
        )
        return {"type": event_type, "reason": reason, "outcome": outcome.to_dict()}


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
