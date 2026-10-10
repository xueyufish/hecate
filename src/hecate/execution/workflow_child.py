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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID

    from hecate.contracts.execution.references import BackendRef
    from hecate.execution.task_control import TaskControlService


@dataclass(frozen=True)
class WorkflowPlan:
    """Parsed ``workflow`` block of one task input."""

    steps: tuple[dict[str, Any], ...]
    on_failure: str  # "fail" (parent fails) | "await" (parent reconciles)


class WorkflowChildTaskAdapter:
    """Named adapter turning a declarative step list into real child tasks."""

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
        for step in steps:
            if not isinstance(step, dict) or not isinstance(step.get("goal"), str) or not step["goal"].strip():
                raise ValueError("every workflow step needs a non-empty string goal")
        on_failure = raw.get("on_failure", "fail")
        if on_failure not in ("fail", "await"):
            raise ValueError("workflow.on_failure must be 'fail' or 'await'")
        return WorkflowPlan(steps=tuple(steps), on_failure=str(on_failure))

    @staticmethod
    def stamp_step_input(goal: str, parent_ref: BackendRef, step_index: int) -> dict[str, Any]:
        """Build the child task input: fresh (never inherits the parent's
        own ``workflow`` block — a child is not itself a workflow parent),
        stamped with the parent reference the terminal hook will call."""

        return {
            "messages": [{"role": "user", "content": goal}],
            "workflow_parent": {"task_ref": parent_ref.to_dict(), "step_index": step_index},
        }

    @staticmethod
    async def submit_step(
        service: TaskControlService,
        workspace_id: UUID,
        agent_id: UUID,
        parent_ref: BackendRef,
        step: dict[str, Any],
        step_index: int,
    ) -> BackendRef:
        """Submit one step as a real child task (system-initiated)."""

        result = await service.submit(
            workspace_id=workspace_id,
            user_id=None,
            goal=str(step["goal"]),
            agent_id=agent_id,
            input=WorkflowChildTaskAdapter.stamp_step_input(str(step["goal"]), parent_ref, step_index),
        )
        return result.task_ref
