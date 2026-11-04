"""Deterministic parent-child workflow adapter (step6e).

The production initiator for the parent-child wait/callback primitives: a
platform task's input payload may declare a deterministic child-step
sequence (``workflow.steps`` — never LLM-decided). The task dispatcher
drives the steps through this adapter: every step is submitted as a REAL
child task via ``TaskControlService.submit`` (system-initiated, stamped
with the parent's task reference), and the parent parks in a durable wait
whose contract binds that child (``await_task_ref``). When the child
reaches a terminal state its own dispatcher finishes and issues the
verified workflow callback from the platform's records — no external
caller is involved (the HTTP callback endpoint stays for external
orchestrators).

Progress lives exclusively in persisted facts: each auto-callback rides
``provide_input`` with ``step_index``/``child_outcome``, so a restarted
dispatcher re-derives the next step from the task's persisted input and
never re-submits a step.

Step modes
----------
``chat`` (default) keeps the historical single-turn child execution. An
explicit ``mode: "controller"`` runs the step as a REAL controller child
on the SAME agent: the full durable controller chain (accepted decisions,
action ledger, reliable waits) with tool feedback driving further turns.
A controller step may declare ``budget_units`` (metered against the
workflow's declared total via the shared quota ledger and the durable
slice ledger — never a parallel one) and ``deadline`` / ``max_turns``
(bound into the child's own controller limits). Delegation
(``agent_children``) stays an Agent-child-only feature: a workflow step
cannot mint further children.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from hecate.core.output_schema import check_output_schema

if TYPE_CHECKING:
    from uuid import UUID

    from hecate.contracts.execution.references import BackendRef
    from hecate.execution.task_control import TaskControlService

CONTROLLER_MODE = "controller"


@dataclass(frozen=True)
class WorkflowPlan:
    """Parsed ``workflow`` block of one task input."""

    steps: tuple[dict[str, Any], ...]
    on_failure: str  # "fail" (parent fails) | "await" (parent reconciles)
    budget_units: int | None = None  # total for controller steps, when declared
    deadline: str | None = None  # workflow-level advance gate, when declared

    @property
    def has_controller_steps(self) -> bool:
        """Whether this workflow uses the durable controller-step contract."""
        return any(step.get("mode") == CONTROLLER_MODE for step in self.steps)


class WorkflowChildTaskAdapter:
    """Named adapter turning a declarative step list into real child tasks."""

    _SUPPORTED_MODES = ("chat", CONTROLLER_MODE)
    _RESERVED_STEP_FIELDS = {"workflow", "workflow_parent", "workflow_step", "provided", "task_ref", "step_index"}
    # The controller slice never mints further children, and the ``controller``
    # block is derived from validated step fields, never caller-authored.
    _FORBIDDEN_STEP_FIELDS = {"agent_children", "controller"}

    @staticmethod
    def _validate_step(step: dict[str, Any], index: int) -> None:
        """Validate one step declaration (step5e extended schema).

        ``goal`` stays required; ``mode`` (default chat), ``output_schema``
        (a JSON Schema object), ``artifact`` (the expected artifact key
        the child result must carry) and ``approval_wait`` (a boolean opting
        the child into durable approval parking) are optional. A
        ``mode: "controller"`` step additionally accepts ``budget_units``,
        ``deadline`` and ``max_turns``. Illegal values are rejected with a
        locatable error — never silently ignored.
        """
        conflict = sorted(WorkflowChildTaskAdapter._RESERVED_STEP_FIELDS.intersection(step))
        if conflict:
            raise ValueError(f"workflow.steps[{index}] conflicts with reserved fields: {', '.join(conflict)}")
        if not isinstance(step.get("goal"), str) or not step["goal"].strip():
            raise ValueError(f"workflow.steps[{index}] needs a non-empty string goal")
        mode = step.get("mode", "chat")
        if mode not in WorkflowChildTaskAdapter._SUPPORTED_MODES:
            raise ValueError(
                f"workflow.steps[{index}].mode must be one of: " + ", ".join(WorkflowChildTaskAdapter._SUPPORTED_MODES)
            )
        forbidden = sorted(WorkflowChildTaskAdapter._FORBIDDEN_STEP_FIELDS.intersection(step))
        if forbidden:
            raise ValueError(
                f"workflow.steps[{index}] must not declare {', '.join(forbidden)}: workflow controller "
                "steps run the declared agent only and derive their controller block from step fields"
            )
        schema = step.get("output_schema")
        if schema is not None:
            if not isinstance(schema, dict):
                raise ValueError(f"workflow.steps[{index}].output_schema must be an object")
            try:
                check_output_schema(schema)
            except ValueError as exc:
                raise ValueError(f"workflow.steps[{index}].output_schema is not a valid JSON Schema: {exc}") from exc
        artifact = step.get("artifact")
        if artifact is not None and (not isinstance(artifact, str) or not artifact.strip()):
            raise ValueError(f"workflow.steps[{index}].artifact must be a non-empty string when present")
        approval_wait = step.get("approval_wait")
        if "approval_wait" in step and not isinstance(approval_wait, bool):
            raise ValueError(f"workflow.steps[{index}].approval_wait must be a boolean when present")
        budget_units = step.get("budget_units")
        if budget_units is not None:
            if mode != CONTROLLER_MODE:
                raise ValueError(f"workflow.steps[{index}].budget_units requires mode='controller'")
            if isinstance(budget_units, bool) or not isinstance(budget_units, int) or budget_units < 1:
                raise ValueError(f"workflow.steps[{index}].budget_units must be a positive integer")
        if (
            "deadline" in step
            and step["deadline"] is not None
            and WorkflowChildTaskAdapter._parse_deadline(step["deadline"]) is None
        ):
            raise ValueError(f"workflow.steps[{index}].deadline must be a valid ISO timestamp")
        max_turns = step.get("max_turns")
        if max_turns is not None:
            if mode != CONTROLLER_MODE:
                raise ValueError(f"workflow.steps[{index}].max_turns requires mode='controller'")
            if isinstance(max_turns, bool) or not isinstance(max_turns, int) or max_turns < 1:
                raise ValueError(f"workflow.steps[{index}].max_turns must be a positive integer")

    @staticmethod
    def _parse_deadline(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            deadline = datetime.fromisoformat(value)
        except ValueError:
            return None
        return deadline if deadline.tzinfo is not None else deadline.replace(tzinfo=UTC)

    @staticmethod
    def plan(payload: dict[str, Any]) -> WorkflowPlan | None:
        """Parse the ``workflow`` block; ``None`` = not a workflow task.

        Malformed declarations raise: a task that declares a workflow must
        never silently degrade into a plain agent run.
        """

        raw = payload.get("workflow")
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ValueError("workflow payload must be an object")
        steps = raw.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError("workflow.steps must be a non-empty array")
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                raise ValueError(f"workflow.steps[{index}] must be an object")
            WorkflowChildTaskAdapter._validate_step(step, index)
        on_failure = raw.get("on_failure", "fail")
        if on_failure not in ("fail", "await"):
            raise ValueError("workflow.on_failure must be 'fail' or 'await'")
        total = raw.get("budget_units")
        if total is not None:
            if isinstance(total, bool) or not isinstance(total, int) or total < 1:
                raise ValueError("workflow.budget_units must be a positive integer")
            undeclared = [i for i, step in enumerate(steps) if step.get("budget_units") is None]
            if undeclared:
                raise ValueError(
                    "workflow.budget_units requires every controller step to declare budget_units "
                    f"(missing at workflow.steps indices {undeclared})"
                )
            if sum(int(step["budget_units"]) for step in steps) > total:
                raise ValueError("workflow step budget_units must sum to at most workflow.budget_units")
        deadline = raw.get("deadline")
        if deadline is not None and WorkflowChildTaskAdapter._parse_deadline(deadline) is None:
            raise ValueError("workflow.deadline must be a valid ISO timestamp")
        return WorkflowPlan(
            steps=tuple(steps),
            on_failure=str(on_failure),
            budget_units=int(total) if total is not None else None,
            deadline=str(deadline) if deadline is not None else None,
        )

    @staticmethod
    def stamp_step_input(
        goal: str,
        parent_ref: BackendRef,
        step_index: int,
        step: dict[str, Any] | None = None,
        *,
        initiator_user_id: str | None = None,
        workflow_deadline: str | None = None,
    ) -> dict[str, Any]:
        """Build the child task input: fresh (never inherits the parent's
        own ``workflow`` block — a child is not itself a workflow parent),
        stamped with the parent reference the terminal hook will call.

        A step declaring ``approval_wait`` opts its child into durable
        approval parking: the flag rides the persisted input so the child
        dispatch assembly enables the durable-wait approval callback
        (step5e; the submit whitelist already carries the key). A
        ``mode: "controller"`` step derives the child's controller block
        from its validated fields (``max_turns``, ``deadline``) and turns
        the durable controller chain on. The child inherits the parent's
        initiator and the tighter of the step and workflow deadlines."""

        step = dict(step or {"goal": goal})
        payload = {
            "messages": [{"role": "user", "content": goal}],
            "workflow_parent": {
                "task_ref": parent_ref.to_dict(),
                "step_index": step_index,
                **(
                    {"initiator_user_id": initiator_user_id}
                    if initiator_user_id and step.get("mode") == CONTROLLER_MODE
                    else {}
                ),
            },
            "workflow_step": step,
            "approval_wait": bool(step.get("approval_wait") is True),
        }
        if step.get("mode") == CONTROLLER_MODE:
            controller: dict[str, Any] = {"max_turns": int(step.get("max_turns") or 8)}
            deadlines = [
                deadline
                for raw in (step.get("deadline"), workflow_deadline)
                if (deadline := WorkflowChildTaskAdapter._parse_deadline(raw)) is not None
            ]
            if deadlines:
                controller["deadline"] = min(deadlines).isoformat()
            payload["controller_loop"] = True
            payload["controller"] = controller
        return payload

    @staticmethod
    def submission_key(parent_ref: BackendRef, step_index: int) -> str:
        """Return the stable identity for one workflow step submission."""
        return f"workflow:{parent_ref.issuer_domain}:{parent_ref.id}:{step_index}"

    @staticmethod
    async def submit_step(
        service: TaskControlService,
        workspace_id: UUID,
        agent_id: UUID,
        parent_ref: BackendRef,
        step: dict[str, Any],
        step_index: int,
        *,
        initiator_user_id: str | None = None,
        workflow_deadline: str | None = None,
    ) -> BackendRef:
        """Submit a real child task under its inherited execution authority."""
        from uuid import UUID

        controller_step = step.get("mode") == CONTROLLER_MODE

        result = await service.submit(
            workspace_id=workspace_id,
            user_id=UUID(initiator_user_id) if controller_step and initiator_user_id else None,
            goal=str(step["goal"]),
            agent_id=agent_id,
            input=WorkflowChildTaskAdapter.stamp_step_input(
                str(step["goal"]),
                parent_ref,
                step_index,
                step,
                initiator_user_id=initiator_user_id,
                workflow_deadline=workflow_deadline,
            ),
            idempotency_key=WorkflowChildTaskAdapter.submission_key(parent_ref, step_index),
            _verified_workflow_step=True,
        )
        return result.task_ref
