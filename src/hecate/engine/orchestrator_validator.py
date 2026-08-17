"""Fail-closed pre-dispatch validation for dynamic orchestration (1.3.18).

The LLM planner can produce plausible-but-broken TaskDAGs: cycles,
references to agents the caller never made available, references to
upstream tasks whose declared outputs do not match, or simply missing
inputs. We refuse to dispatch the resulting sub-graph rather than
executing a partially valid plan (OMA v1.14 behavior; cf.
``validateTaskRequirements``).

Call sites:
  * CoordinatorWorker — after each planner iteration, before
    emitting the ORCHESTRATOR_DECISION and invoking the executor.
  * Canvas save path — same check on the user-edited DAG so designers
    see the same errors as the runtime would.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from hecate.engine.dynamic_types import (
    TaskDAG,
    TaskDAGValidationError,
    TaskNode,
)

# ---------------------------------------------------------------------------
# Roster adapter
# ---------------------------------------------------------------------------


AgentCapability = str  # e.g. "tool:web_search", "knowledge:legal_corpus"


class RosterEntry:
    """Lightweight adapter around any roster agent descriptor.

    We accept Pydantic-style objects with the attributes we need or a
    plain dict. Other code may pass AgentModel / AgentTemplate instances
    directly. ``agent_id`` is the only required field; capabilities and
    model are optional and default to empty.
    """

    __slots__ = ("agent_id", "capabilities", "model")

    def __init__(
        self,
        agent_id: str,
        capabilities: Iterable[str] | None = None,
        model: str | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.capabilities: frozenset[str] = frozenset(capabilities or ())
        self.model = model

    @classmethod
    def from_value(cls, value: Any) -> RosterEntry:
        if isinstance(value, cls):
            return value
        agent_id = _maybe_attr(value, "agent_id") or _maybe_attr(value, "id")
        if not agent_id:
            msg = f"Cannot derive agent_id from roster entry: {value!r}"
            raise TaskDAGValidationError("invalid_roster_entry", msg)
        capabilities = _maybe_attr(value, "capabilities") or ()
        model = _maybe_attr(value, "model")
        return cls(agent_id=agent_id, capabilities=capabilities, model=model)


def _maybe_attr(obj: Any, name: str) -> Any:
    if hasattr(obj, name):
        return getattr(obj, name)
    if hasattr(obj, "get"):
        return obj.get(name)
    return None


# ---------------------------------------------------------------------------
# Validation report
# ---------------------------------------------------------------------------


class ValidationIssue:
    """One validation issue. Carries enough structure to round-trip
    into ORCHESTRATOR_DECISION payloads."""

    __slots__ = ("code", "message", "task_id")

    def __init__(self, code: str, message: str, *, task_id: str | None = None) -> None:
        self.code = code
        self.message = message
        self.task_id = task_id

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.task_id is not None:
            out["task_id"] = self.task_id
        return out

    def __repr__(self) -> str:
        return f"ValidationIssue({self.code!r}, {self.message!r}, task_id={self.task_id!r})"


class ValidationReport:
    """Structured outcome of ``validate_task_requirements``.

    ``is_valid`` is True iff ``issues`` is empty. Validation NEVER raises
    for expected fail-closed conditions — callers branch on ``is_valid``.
    """

    def __init__(self, issues: Iterable[ValidationIssue] = ()) -> None:
        self.issues: list[ValidationIssue] = list(issues)

    @property
    def is_valid(self) -> bool:
        return not self.issues

    def extend(self, issues: Iterable[ValidationIssue]) -> None:
        self.issues.extend(issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "issues": [i.to_dict() for i in self.issues],
        }

    def __bool__(self) -> bool:  # truthy iff valid
        return self.is_valid


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _declared_outputs(task: TaskNode) -> set[str]:
    """A task declares exactly one output: ``expected_output``."""
    return {task.expected_output}


def _topological_levels(dag: TaskDAG) -> list[list[str]]:
    """Group task ids into dependency levels for ``max_concurrent`` batching.

    Level 0 has no upstream; level N has at least one upstream at level
    N-1. Used to bound the per-iteration dispatch count and to surface
    cycle faults.
    """
    indegree: dict[str, int] = {t.id: 0 for t in dag.tasks}
    for task_id, upstream in dag.dependencies.items():
        indegree[task_id] = len(upstream)

    levels: list[list[str]] = []
    placed: set[str] = set()
    remaining = dict(indegree)
    while remaining:
        ready = sorted(tid for tid, deg in remaining.items() if deg == 0 and tid not in placed)
        if not ready:
            msg = "Dependency cycle detected during level partitioning"
            raise TaskDAGValidationError("cycle", msg)
        levels.append(ready)
        for tid in ready:
            placed.add(tid)
            remaining.pop(tid, None)
        # Decrement downstream indegrees as their upstream tasks land.
        for task_id, upstream in dag.dependencies.items():
            if task_id in remaining and all(u in placed for u in upstream):
                remaining[task_id] = 0
    return levels


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_task_requirements(
    dag: TaskDAG,
    roster: Iterable[Any],
) -> ValidationReport:
    """Validate a planner-produced TaskDAG against a concrete agent set.

    Returns a ValidationReport. NEVER raises for fail-closed conditions
    (those are encoded as issues with a stable code). Raises only for
    structurally invalid inputs (empty goal, missing fields) where
    Pydantic would also have raised during model construction.
    """
    report = ValidationReport()
    entries = [RosterEntry.from_value(r) for r in roster]
    by_id = {e.agent_id: e for e in entries}

    # 1) Cycle detection (multi-node).
    try:
        levels = _topological_levels(dag)
    except TaskDAGValidationError as exc:
        report.issues.append(ValidationIssue(exc.code, str(exc), task_id=exc.task_id))
        return report

    flat_ids = {tid for level in levels for tid in level}
    expected = {t.id for t in dag.tasks}
    if flat_ids != expected:
        missing = sorted(expected - flat_ids)
        report.issues.append(
            ValidationIssue(
                "unreachable_task",
                f"Tasks not reachable from entry: {missing}",
            )
        )

    # 2) Assignment + capability checks per task.
    for task in dag.tasks:
        entry = by_id.get(task.agent_id)
        if entry is None:
            report.issues.append(
                ValidationIssue(
                    "unsatisfiable_requirement",
                    f"Task '{task.id}' requires agent_id '{task.agent_id}' which is not in the roster",
                    task_id=task.id,
                )
            )
            continue

        # Tasks may declare required capabilities via ``inputs`` with a
        # reserved "<capability>" convention; we conservatively treat
        # any input key starting with "requires:" as a capability
        # requirement.
        for input_key, value in task.inputs.items():
            if input_key.startswith("requires:"):
                capability = value.removeprefix("requires:").strip()
                if capability and capability not in entry.capabilities:
                    report.issues.append(
                        ValidationIssue(
                            "unsatisfiable_requirement",
                            (
                                f"Task '{task.id}' requires capability "
                                f"'{capability}' which agent "
                                f"'{entry.agent_id}' does not expose"
                            ),
                            task_id=task.id,
                        )
                    )

        # 3) Input reference output names must match upstream declarations.
        declared_outputs: dict[str, set[str]] = {t.id: _declared_outputs(t) for t in dag.tasks}
        for local_name, reference in task.inputs.items():
            if reference.startswith("requires:"):
                continue
            if "." not in reference:
                report.issues.append(
                    ValidationIssue(
                        "malformed_input_reference",
                        (
                            f"Task '{task.id}' input '{local_name}' "
                            f"references '{reference}', expected "
                            f"'<task_id>.<output_name>'"
                        ),
                        task_id=task.id,
                    )
                )
                continue
            upstream_id, output_name = reference.split(".", 1)
            if upstream_id not in declared_outputs:
                # Carryover reference — only allowed if the upstream task
                # id is explicitly listed in dag.carryover_outputs. The
                # runtime will hydrate the value from the parent
                # iteration's ledger; the executor enforces format.
                if upstream_id not in dag.carryover_outputs:
                    report.issues.append(
                        ValidationIssue(
                            "dangling_input_reference",
                            f"Task '{task.id}' input '{local_name}' references unknown upstream '{upstream_id}'",
                            task_id=task.id,
                        )
                    )
                    continue
                # Carryover allowed — cannot verify against declared
                # outputs of an absent task; skip the output-name check.
                continue
            if output_name not in declared_outputs[upstream_id]:
                report.issues.append(
                    ValidationIssue(
                        "output_name_mismatch",
                        (
                            f"Task '{task.id}' input '{local_name}' "
                            f"references output '{output_name}' from "
                            f"'{upstream_id}', which only declares "
                            f"{sorted(declared_outputs[upstream_id])}"
                        ),
                        task_id=task.id,
                    )
                )

        # 4) verify.verifier_agent_id must be in the roster.
        if task.verify is not None and task.verify.verifier_agent_id not in by_id:
            report.issues.append(
                ValidationIssue(
                    "unsatisfiable_requirement",
                    (
                        f"Task '{task.id}' verify hook requires "
                        f"verifier_agent_id '{task.verify.verifier_agent_id}' "
                        f"which is not in the roster"
                    ),
                    task_id=task.id,
                )
            )

    return report


def budget_topological_levels(dag: TaskDAG) -> list[list[str]]:
    """Public helper used by the executor: topologically-grouped task ids.

    Equivalent to the internal ``_topological_levels`` but exposed so the
    executor does not need to know about the validator's private API.
    """
    return _topological_levels(dag)


def validate_budgets(dag: TaskDAG) -> list[ValidationIssue]:
    """Lightweight static check of the TaskDAG against its declared budgets.

    Returns a list of issues; empty means all budget fields can be honored
    by the executor (the runtime also re-checks at dispatch boundaries).
    """
    issues: list[ValidationIssue] = []
    levels = _topological_levels(dag)
    total = len(dag.tasks)
    if total > dag.budgets.max_total_tasks:
        issues.append(
            ValidationIssue(
                "budget_max_total_tasks",
                f"DAG has {total} tasks but max_total_tasks={dag.budgets.max_total_tasks}",
            )
        )
    for level in levels:
        if len(level) > dag.budgets.max_concurrent:
            issues.append(
                ValidationIssue(
                    "budget_max_concurrent",
                    f"Dependency level {level} has {len(level)} tasks but max_concurrent={dag.budgets.max_concurrent}",
                )
            )
    return issues


__all__ = [
    "AgentCapability",
    "RosterEntry",
    "ValidationIssue",
    "ValidationReport",
    "budget_topological_levels",
    "validate_budgets",
    "validate_task_requirements",
]
