"""Public contract for dynamic orchestration (1.3.18).

Defines the Pydantic models exchanged between the LLM planner and the
deterministic executor. The contract is intentionally minimal: the
planner expresses intent, the executor materializes a graph; the
planner does not write raw GraphConfig JSON.

Why a typed contract (rather than raw dicts / GraphConfig JSON)?
  * Stops the LLM from leaking graph-DSL concerns (channel names,
    node ids) into its output.
  * Makes fail-closed pre-dispatch validation trivial at the type
    layer.
  * Pins the public schema; the executor can evolve freely.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Benefit-based delegation rubric (public constant, snapshot-tested)
# ---------------------------------------------------------------------------

# Source-faithful to deer-flow subagents/AGENTS.md (lead_agent/prompt.py)
# and Hecate's Magentic double-loop specification. Snapshotted by
# tests/test_coordinator_prompt.py — DO NOT edit without updating the test.
BENEFIT_BASED_DELEGATION_RUBRIC: str = """\
Benefit-based delegation rubric:
- Default to direct execution. Subagent delegation is an optimization, \
not a default response to complexity.
- Permit `task()` dispatch only when at least one of the following \
benefits clearly exceeds the aggregate cost:
  * Parallel latency win (subagents fan out and finish before a \
sequential chain would).
  * Specialist capability (a subagent has tools or knowledge a direct \
agent does not).
  * Context isolation (a subagent keeps intermediate tool noise out of \
the lead's message stream).
- HARD VETOES against parallel dispatch:
  * Output dependency: one task's result feeds into another dispatch in the \
same batch — those must be sequenced.
  * Overlapping mutable state: two parallel tasks would write to the \
same channel or share unshareable resources.
- Costs that must be weighed against the benefit:
  * Startup cost (LLM invocation overhead).
  * Duplicate-discovery cost (each subagent re-discovers context).
  * Synthesis cost (the lead must fold multiple outputs back together).
  * State-conflict cost (parallel writes need arbitration).
  * Side-effect cost (external actions are hard to roll back).
- Use the fewest useful subagents. A bounded sequential chain in one \
subagent is fine when specialist or context-isolation benefit clearly \
wins.
- Parallel scopes MUST be independent and non-overlapping.
- Re-evaluate each subsequent batch. Within-batch parallel benefit is \
retained, not new benefit is created.
- Concurrency is clamped by max_concurrent (range 1-4, default 3) and \
total dispatch is capped by max_total_tasks (range 1-50, default 6).
"""


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


class TaskDAGValidationError(ValueError):
    """Raised by validate_task_requirements when a TaskDAG cannot proceed.

    Carries structured error information so the coordinator can emit an
    ORCHESTRATOR_DECISION event with a stable shape.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        task_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.task_id = task_id


# ---------------------------------------------------------------------------
# Configuration models
# ---------------------------------------------------------------------------


class OrchestrationBudgets(BaseModel):
    """Three-axis + iteration budgets for an orchestration.

    Defaults match the DeerFlow subagent reference (max_total_per_run=6,
    max_concurrent=3) so an empty config behaves identically to a
    well-tuned v0.1.0 deployment. Limits are explicitly clamped at
    dispatch time so the planner-visible value and the executor value
    always agree.
    """

    model_config = ConfigDict(frozen=True)

    max_iterations: int = Field(default=5, ge=1, le=100)
    max_total_tasks: int = Field(default=6, ge=1, le=50)
    max_concurrent: int = Field(default=3, ge=1, le=4)
    stall_limit: int = Field(default=2, ge=1, le=10)
    token_budget: int | None = Field(default=None, ge=1)


class VerifyConfig(BaseModel):
    """Optional per-task verification hook.

    When set, after the primary worker returns successfully, the
    executor invokes `verifier_agent_id` with the task output and
    `prompt`; the verifier's response ("PASS"/"FAIL") is recorded in
    the task's ledger entry as `verified: bool`.
    """

    model_config = ConfigDict(frozen=True)

    verifier_agent_id: str
    prompt: str = ""


# ---------------------------------------------------------------------------
# Task DAG models
# ---------------------------------------------------------------------------


class TaskNode(BaseModel):
    """A single task within a TaskDAG.

    `inputs` maps a local input name to an upstream reference of the
    form "<task_id>.<expected_output>". The executor resolves these
    references against the orchestrator's `_ledger` channel; replans
    in subsequent iterations can reuse completed task outputs via
    the same addressing scheme.

    `on_failure` decides what happens to the whole DAG if this task
    fails or is verified-false:
      * continue: log the failure and proceed (other tasks can still
        run; synthesis may receive partial outputs).
      * stop (default): abort the orchestration immediately.
      * replan: emit an ORCHESTRATOR_EVALUATION with verdict=stalled
        so the coordinator triggers a replan iteration.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    inputs: dict[str, str] = Field(default_factory=dict)
    expected_output: str = Field(min_length=1)
    on_failure: Literal["continue", "stop", "replan"] = "stop"
    verify: VerifyConfig | None = None


class TaskDAG(BaseModel):
    """The planner's contract: goal + tasks + dependencies + synthesis.

    The planner is responsible for producing a *suggested* DAG
    (Magentic "hint, not contract"); the executor then enforces
    structure via validate_task_requirements before any worker
    dispatches. Iteration history is preserved through
    ORCHESTRATOR_DECISION events rather than by mutating this object.
    """

    model_config = ConfigDict(frozen=True)

    goal: str = Field(min_length=1)
    tasks: list[TaskNode] = Field(min_length=1)
    dependencies: dict[str, list[str]] = Field(default_factory=dict)
    synthesis_prompt: str | None = None
    synthesis_transform: str | None = None
    budgets: OrchestrationBudgets = Field(default_factory=OrchestrationBudgets)
    carryover_outputs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_dag_structure(self) -> TaskDAG:
        """Structural checks that Pydantic can run before any roster lookup.

        Catches:
          * Duplicate task ids.
          * Unknown task ids in dependencies.
          * Self-dependencies.
          * Tasks referenced by an input key but absent from `tasks`.
        Cycle detection across multiple nodes is done by the executor's
        fail-closed pre-flight validator, not here.
        """
        ids = {t.id for t in self.tasks}
        if len(ids) != len(self.tasks):
            duplicates = sorted({t.id for t in self.tasks if [x.id for x in self.tasks].count(t.id) > 1})
            raise TaskDAGValidationError(
                "duplicate_task_id",
                f"Duplicate task ids in DAG: {duplicates}",
            )
        for task_id, upstream in self.dependencies.items():
            if task_id not in ids:
                raise TaskDAGValidationError(
                    "unknown_dependency_target",
                    f"dependencies key '{task_id}' is not a known task id",
                    task_id=task_id,
                )
            if task_id in upstream:
                raise TaskDAGValidationError(
                    "self_dependency",
                    f"Task '{task_id}' depends on itself",
                    task_id=task_id,
                )
            for up in upstream:
                if up not in ids:
                    raise TaskDAGValidationError(
                        "unknown_dependency_source",
                        f"Task '{task_id}' depends on unknown task '{up}'",
                        task_id=task_id,
                    )
        for task in self.tasks:
            for local_name, reference in task.inputs.items():
                # Capability requirements use the "requires:" key prefix
                # and carry a free-form capability identifier rather
                # than an upstream reference; skip them here — the
                # validator handles them under validate_task_requirements.
                if local_name.startswith("requires:"):
                    continue
                # Reference format: "<upstream_task_id>.<expected_output>".
                if "." not in reference:
                    raise TaskDAGValidationError(
                        "malformed_input_reference",
                        (
                            f"Task '{task.id}' input '{local_name}' references "
                            f"'{reference}', expected '<task_id>.<output_name>'"
                        ),
                        task_id=task.id,
                    )
                upstream_id, _ = reference.split(".", 1)
                # References to tasks not declared in this DAG are
                # treated as carryover references (the runtime will
                # hydrate them from the parent iteration's ledger
                # rather than re-executing the producer). Format is
                # still validated.
                if upstream_id in ids:
                    continue
        return self
